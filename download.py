"""Download new clips from a specific Arlo camera to the local clips directory."""

import argparse
import json
import logging
import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from scripts.arlo_client import connect_arlo

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
SESSION_DIR = Path(os.getenv("SESSION_DIR", ".arlo_session"))
CAMERA_NAME = os.environ["ARLO_CAMERA_NAME"]
DAYS_BACK = int(os.getenv("DAYS_BACK", "1"))
MIN_CLIP_DURATION_SECONDS = int(os.getenv("MIN_CLIP_DURATION_SECONDS", "5"))
EASTERN_TZ = ZoneInfo("America/New_York")


def parse_env_array(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        value = parsed.strip()
        return [value] if value else []

    return [item.strip() for item in raw.split(",") if item.strip()]


EXCLUDED_OBJ_CATEGORIES = parse_env_array("EXCLUDED_OBJ_CATEGORIES")
EXCLUDED_OBJ_CATEGORY_KEYS = {value.casefold() for value in EXCLUDED_OBJ_CATEGORIES}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--latest",
        type=int,
        metavar="N",
        help="Only download the most recent N videos within the selected library window.",
    )
    args = parser.parse_args()
    if args.latest is not None and args.latest < 1:
        parser.error("--latest must be a positive integer")
    return args


def clip_filename(created_at_ms: int) -> str:
    dt = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).astimezone(
        EASTERN_TZ
    )
    return dt.strftime("%Y%m%d_%H%M%S_%Z") + ".mp4"


def get_mp4_duration(mp4: Path) -> float | None:
    """Parse duration from MP4 mvhd box. Returns None if unreadable."""
    try:
        data = mp4.read_bytes()
    except OSError:
        return None

    pos = 0
    while pos < len(data) - 8:
        box_size = struct.unpack_from(">I", data, pos)[0]
        box_type = data[pos + 4 : pos + 8]

        if box_size < 8:
            break

        if box_type == b"moov":
            pos += 8
            continue

        if box_type == b"mvhd":
            mvhd = data[pos + 8 :]
            version = mvhd[0]
            if version == 1:
                timescale = struct.unpack_from(">I", mvhd, 20)[0]
                duration = struct.unpack_from(">Q", mvhd, 24)[0]
            else:
                timescale = struct.unpack_from(">I", mvhd, 12)[0]
                duration = struct.unpack_from(">I", mvhd, 16)[0]
            if timescale == 0:
                return None
            return duration / timescale

        pos += box_size

    return None


def download_clip(recording, dest: Path) -> None:
    tmp = dest.with_suffix(".tmp")
    ok = recording.download_video(str(tmp))
    if not ok:
        raise RuntimeError("pyaarlo download_video() returned False")
    tmp.rename(dest)


def get_obj_categories(attrs: dict) -> list[str]:
    value = attrs.get("objCategory")
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def main() -> None:
    args = parse_args()
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(exist_ok=True)

    log.info("Connecting to Arlo...")
    ar = connect_arlo(library_days=DAYS_BACK, storage_dir=SESSION_DIR)

    camera = next((c for c in ar.cameras if c.name == CAMERA_NAME), None)
    if camera is None:
        available = [c.name for c in ar.cameras]
        raise RuntimeError(
            f'Camera "{CAMERA_NAME}" not found. Available cameras: {available}'
        )
    log.info('Found camera "%s" (device_id=%s)', CAMERA_NAME, camera.device_id)

    log.info("Fetching library (last %d day(s))...", DAYS_BACK)
    camera.update_media(wait=True)
    _, recordings = ar.ml.videos_for(camera)
    if EXCLUDED_OBJ_CATEGORIES:
        log.info(
            "Excluding recordings with objCategory in %s",
            EXCLUDED_OBJ_CATEGORIES,
        )
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    recordings = [
        r
        for r in recordings
        if r.created_at is not None
        and datetime.fromtimestamp(r.created_at / 1000, tz=timezone.utc) >= cutoff
    ]
    recordings.sort(key=lambda r: r.created_at, reverse=True)
    if args.latest is not None:
        recordings = recordings[: args.latest]
        log.info("Limiting download set to the most recent %d recording(s).", args.latest)
    log.info(
        'Found %d recording(s) for camera "%s"', len(recordings), CAMERA_NAME
    )

    downloaded = 0
    for rec in recordings:
        filename = clip_filename(rec.created_at)
        dest = CLIPS_DIR / filename
        if dest.exists():
            log.debug("Already have %s, skipping.", filename)
            continue

        # Pre-filter using metadata when available (saves a download)
        attrs = getattr(rec, "_attrs", {})
        obj_categories = get_obj_categories(attrs) if isinstance(attrs, dict) else []
        excluded_categories = [
            category
            for category in obj_categories
            if category.casefold() in EXCLUDED_OBJ_CATEGORY_KEYS
        ]
        if excluded_categories:
            log.info(
                "Skipping %s due to excluded objCategory %s",
                filename,
                excluded_categories,
            )
            continue
        clip_secs = attrs.get("mediaDurationSecond") if isinstance(attrs, dict) else None
        if clip_secs is not None and clip_secs < MIN_CLIP_DURATION_SECONDS:
            log.info("Skipping %s (%ds < %ds minimum, from metadata)", filename, clip_secs, MIN_CLIP_DURATION_SECONDS)
            continue

        log.info("Downloading %s...", filename)
        try:
            download_clip(rec, dest)
        except Exception as e:
            log.error("Failed to download %s: %s", filename, e)
            continue

        # Verify actual duration from the file (metadata is often missing/wrong)
        duration = get_mp4_duration(dest)
        if duration is not None and duration < MIN_CLIP_DURATION_SECONDS:
            log.info("Deleting %s (%.1fs < %ds minimum)", filename, duration, MIN_CLIP_DURATION_SECONDS)
            dest.unlink()
            continue

        downloaded += 1

    log.info("Downloaded %d new clip(s).", downloaded)


if __name__ == "__main__":
    main()

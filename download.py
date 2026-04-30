"""Download new clips from a specific Arlo camera to the local clips directory."""

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pyaarlo
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("arlo_watch.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
SESSION_DIR = Path(os.getenv("SESSION_DIR", ".arlo_session"))
CAMERA_NAME = os.environ["ARLO_CAMERA_NAME"]
DAYS_BACK = int(os.getenv("DAYS_BACK", "1"))
MIN_CLIP_DURATION_SECONDS = int(os.getenv("MIN_CLIP_DURATION_SECONDS", "5"))
EASTERN_TZ = ZoneInfo("America/New_York")


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


def connect_arlo() -> pyaarlo.PyArlo:
    return pyaarlo.PyArlo(
        username=os.environ["ARLO_USERNAME"],
        password=os.environ["ARLO_PASSWORD"],
        library_days=max(DAYS_BACK, 1),
        synchronous_mode=True,
        tfa_source=os.getenv("ARLO_TFA_SOURCE", "console"),
        tfa_type=os.getenv("ARLO_TFA_TYPE", "email"),
        storage_dir=str(SESSION_DIR),
    )


def clip_filename(created_at_ms: int) -> str:
    dt = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).astimezone(
        EASTERN_TZ
    )
    return dt.strftime("%Y%m%d_%H%M%S_%Z") + ".mp4"


def download_clip(recording, dest: Path) -> None:
    tmp = dest.with_suffix(".tmp")
    ok = recording.download_video(str(tmp))
    if not ok:
        raise RuntimeError("pyaarlo download_video() returned False")
    tmp.rename(dest)


def main() -> None:
    args = parse_args()
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(exist_ok=True)

    log.info("Connecting to Arlo...")
    ar = connect_arlo()

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
        clip_secs = getattr(rec, "mediaDurationSecond", None)
        if clip_secs is not None and clip_secs < MIN_CLIP_DURATION_SECONDS:
            log.info("Skipping %s (%ds < %ds minimum)", filename, clip_secs, MIN_CLIP_DURATION_SECONDS)
            continue
        log.info("Downloading %s...", filename)
        try:
            download_clip(rec, dest)
            downloaded += 1
        except Exception as e:
            log.error("Failed to download %s: %s", filename, e)

    log.info("Downloaded %d new clip(s).", downloaded)


if __name__ == "__main__":
    main()

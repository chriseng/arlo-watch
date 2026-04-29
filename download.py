"""Download new clips from a specific Arlo camera to the local clips directory."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pyaarlo
import requests
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

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "clips"))
SESSION_DIR = Path(os.getenv("SESSION_DIR", ".arlo_session"))
CAMERA_NAME = os.environ["ARLO_CAMERA_NAME"]
DAYS_BACK = int(os.getenv("DAYS_BACK", "1"))


def connect_arlo() -> pyaarlo.PyArlo:
    return pyaarlo.PyArlo(
        username=os.environ["ARLO_USERNAME"],
        password=os.environ["ARLO_PASSWORD"],
        tfa_source=os.getenv("ARLO_TFA_SOURCE", "console"),
        tfa_type=os.getenv("ARLO_TFA_TYPE", "email"),
        storage_dir=str(SESSION_DIR),
    )


def clip_filename(created_at_ms: int) -> str:
    dt = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d_%H%M%S_UTC") + ".mp4"


def download_clip(url: str, dest: Path) -> None:
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()
    tmp = dest.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        for chunk in response.iter_content(chunk_size=65536):
            f.write(chunk)
    tmp.rename(dest)


def main() -> None:
    CLIPS_DIR.mkdir(exist_ok=True)
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
    recordings = [
        r for r in ar.library(days=DAYS_BACK) if r.device_id == camera.device_id
    ]
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
        log.info("Downloading %s...", filename)
        try:
            download_clip(rec.video_url, dest)
            downloaded += 1
        except Exception as e:
            log.error("Failed to download %s: %s", filename, e)

    log.info("Downloaded %d new clip(s).", downloaded)


if __name__ == "__main__":
    main()

"""Delete mp4 clips older than CLIP_RETENTION_DAYS, with optional sidecar cleanup."""

import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
CLIP_RETENTION_DAYS = int(os.getenv("CLIP_RETENTION_DAYS", "7"))
_FILENAME_TZS = {
    "UTC": timezone.utc,
    "EDT": timezone(timedelta(hours=-4)),
    "EST": timezone(timedelta(hours=-5)),
}

# Matches filenames like 20260429_181132_EDT.mp4
_FILENAME_RE = re.compile(r"^(\d{8}_\d{6})_(EDT|EST|UTC)\.mp4$")


def capture_time(mp4: Path) -> datetime | None:
    """Parse capture datetime from filename (YYYYMMDD_HHMMSS_TZ). Returns UTC-aware datetime or None."""
    m = _FILENAME_RE.match(mp4.name)
    if not m:
        return None
    try:
        captured = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    local_tz = _FILENAME_TZS[m.group(2)]
    return captured.replace(tzinfo=local_tz).astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips-dir", type=Path, default=CLIPS_DIR)
    parser.add_argument("--retention-days", type=int, default=CLIP_RETENTION_DAYS)
    parser.add_argument("--purge-summaries", action="store_true", help="Also delete .json sidecar files")
    parser.add_argument("--purge-screenshots", action="store_true", help="Also delete .jpg sidecar files")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")
    args = parser.parse_args()

    clips_dir: Path = args.clips_dir
    if not clips_dir.is_dir():
        raise SystemExit(f"Clips directory not found: {clips_dir}")

    now = datetime.now(tz=timezone.utc)
    cutoff_seconds = args.retention_days * 86400
    mp4s = sorted(clips_dir.glob("*.mp4"))
    print(f"Checking {len(mp4s)} clips in {clips_dir} (retention: {args.retention_days} days)")

    removed = 0
    skipped = 0
    for mp4 in mp4s:
        captured = capture_time(mp4)
        if captured is None:
            print(f"  warning: cannot parse capture time from {mp4.name}, skipping")
            skipped += 1
            continue

        age_seconds = (now - captured).total_seconds()
        if age_seconds < cutoff_seconds:
            continue

        age_days = age_seconds / 86400
        print(f"  {mp4.name}: {age_days:.1f} days old")

        targets = [mp4]
        if args.purge_summaries:
            json_file = mp4.with_suffix(".json")
            if json_file.exists():
                targets.append(json_file)
        if args.purge_screenshots:
            jpg_file = mp4.with_suffix(".jpg")
            if jpg_file.exists():
                targets.append(jpg_file)

        for path in targets:
            if args.dry_run:
                print(f"    [dry-run] would delete {path}")
            else:
                path.unlink()
                print(f"    deleted {path}")

        removed += 1

    label = "would remove" if args.dry_run else "removed"
    print(f"\nDone. {label} {removed} clip(s)." + (f" Skipped {skipped} unparseable." if skipped else ""))


if __name__ == "__main__":
    main()

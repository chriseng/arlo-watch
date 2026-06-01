"""Delete expired clips and timestamped sidecar artifacts based on retention windows."""

import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
CLIP_RETENTION_DAYS = int(os.getenv("CLIP_RETENTION_DAYS", "7"))
SCREENSHOT_RETENTION_DAYS = int(os.getenv("SCREENSHOT_RETENTION_DAYS", "30"))
_FILENAME_TZS = {
    "UTC": timezone.utc,
    "EDT": timezone(timedelta(hours=-4)),
    "EST": timezone(timedelta(hours=-5)),
}

# Matches filenames like 20260429_181132_EDT.mp4, 20260429_181132_EDT.json, or 20260429_181132_EDT.jpg
_TIMESTAMPED_FILE_RE = re.compile(r"^(\d{8}_\d{6})_(EDT|EST|UTC)(?:\..+)?$")


def capture_time(path: Path) -> datetime | None:
    """Parse capture datetime from filename (YYYYMMDD_HHMMSS_TZ...). Returns UTC-aware datetime or None."""
    m = _TIMESTAMPED_FILE_RE.match(path.name)
    if not m:
        return None
    try:
        captured = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    local_tz = _FILENAME_TZS[m.group(2)]
    return captured.replace(tzinfo=local_tz).astimezone(timezone.utc)


def delete_paths(paths: list[Path], dry_run: bool) -> None:
    for path in paths:
        if dry_run:
            print(f"    [dry-run] would delete {path}")
        else:
            path.unlink()
            print(f"    deleted {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips-dir", type=Path, default=CLIPS_DIR)
    parser.add_argument("--retention-days", type=int, default=CLIP_RETENTION_DAYS)
    parser.add_argument(
        "--screenshot-retention-days",
        type=int,
        default=SCREENSHOT_RETENTION_DAYS,
        help="Delete timestamped .json and .jpg artifacts older than this many days",
    )
    parser.add_argument("--purge-summaries", action="store_true", help="Also delete .json sidecar files")
    parser.add_argument("--purge-screenshots", action="store_true", help="Also delete .jpg sidecar files")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")
    args = parser.parse_args()

    clips_dir: Path = args.clips_dir
    if not clips_dir.is_dir():
        raise SystemExit(f"Clips directory not found: {clips_dir}")

    now = datetime.now(tz=timezone.utc)
    clip_cutoff_seconds = args.retention_days * 86400
    sidecar_cutoff_seconds = args.screenshot_retention_days * 86400
    mp4s = sorted(clips_dir.glob("*.mp4"))
    print(f"Checking {len(mp4s)} clips in {clips_dir} (retention: {args.retention_days} days)")

    removed_clips = 0
    removed_sidecars = 0
    skipped = 0
    for mp4 in mp4s:
        captured = capture_time(mp4)
        if captured is None:
            print(f"  warning: cannot parse capture time from {mp4.name}, skipping")
            skipped += 1
            continue

        age_seconds = (now - captured).total_seconds()
        if age_seconds < clip_cutoff_seconds:
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

        delete_paths(targets, args.dry_run)
        removed_clips += 1

    sidecar_files = sorted(
        path
        for pattern in ("*.json", "*.jpg")
        for path in clips_dir.glob(pattern)
        if capture_time(path) is not None and not path.with_suffix(".mp4").exists()
    )
    print(
        f"Checking {len(sidecar_files)} standalone screenshot/summary file(s) in {clips_dir} "
        f"(retention: {args.screenshot_retention_days} days)"
    )
    for path in sidecar_files:
        captured = capture_time(path)
        if captured is None:
            continue

        age_seconds = (now - captured).total_seconds()
        if age_seconds < sidecar_cutoff_seconds:
            continue

        age_days = age_seconds / 86400
        print(f"  {path.name}: {age_days:.1f} days old")
        delete_paths([path], args.dry_run)
        removed_sidecars += 1

    label = "would remove" if args.dry_run else "removed"
    print(
        f"\nDone. {label} {removed_clips} clip(s) and {removed_sidecars} screenshot/summary file(s)."
        + (f" Skipped {skipped} unparseable." if skipped else "")
    )


if __name__ == "__main__":
    main()

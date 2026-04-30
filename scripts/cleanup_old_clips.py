"""Delete mp4 clips older than CLIP_RETENTION_DAYS, with optional sidecar cleanup."""

import argparse
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
CLIP_RETENTION_DAYS = int(os.getenv("CLIP_RETENTION_DAYS", "7"))


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

    cutoff = time.time() - args.retention_days * 86400
    mp4s = sorted(clips_dir.glob("*.mp4"))
    print(f"Checking {len(mp4s)} clips in {clips_dir} (retention: {args.retention_days} days)")

    removed = 0
    for mp4 in mp4s:
        if mp4.stat().st_mtime >= cutoff:
            continue

        age_days = (time.time() - mp4.stat().st_mtime) / 86400
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
    print(f"\nDone. {label} {removed} clip(s).")


if __name__ == "__main__":
    main()

"""Delete .mp4 clips shorter than MIN_CLIP_DURATION_SECONDS, plus their .jpg and .json sidecar files."""

import argparse
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
MIN_CLIP_DURATION_SECONDS = int(os.getenv("MIN_CLIP_DURATION_SECONDS", "5"))


def get_duration(mp4: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(mp4),
        ],
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    if not raw:
        raise ValueError(f"ffprobe returned no duration for {mp4}: {result.stderr.strip()}")
    return float(raw)


def delete_clip(mp4: Path, dry_run: bool) -> None:
    sidecars = [mp4.with_suffix(".jpg"), mp4.with_suffix(".json")]
    targets = [mp4] + [s for s in sidecars if s.exists()]
    for path in targets:
        if dry_run:
            print(f"  [dry-run] would delete {path}")
        else:
            path.unlink()
            print(f"  deleted {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips-dir", type=Path, default=CLIPS_DIR)
    parser.add_argument("--min-duration", type=float, default=MIN_CLIP_DURATION_SECONDS)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")
    args = parser.parse_args()

    clips_dir: Path = args.clips_dir
    if not clips_dir.is_dir():
        raise SystemExit(f"Clips directory not found: {clips_dir}")

    mp4s = sorted(clips_dir.glob("*.mp4"))
    print(f"Checking {len(mp4s)} clips in {clips_dir} (min duration: {args.min_duration}s)")

    removed = 0
    for mp4 in mp4s:
        try:
            duration = get_duration(mp4)
        except ValueError as e:
            print(f"  warning: {e}")
            continue

        if duration < args.min_duration:
            print(f"  {mp4.name}: {duration:.1f}s < {args.min_duration}s")
            delete_clip(mp4, args.dry_run)
            removed += 1

    label = "would remove" if args.dry_run else "removed"
    print(f"\nDone. {label} {removed} clip(s).")


if __name__ == "__main__":
    main()

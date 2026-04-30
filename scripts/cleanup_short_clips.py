"""Delete .mp4 clips shorter than MIN_CLIP_DURATION_SECONDS, plus their .jpg and .json sidecar files."""

import argparse
import os
import struct
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
MIN_CLIP_DURATION_SECONDS = int(os.getenv("MIN_CLIP_DURATION_SECONDS", "5"))


def get_duration(mp4: Path) -> float:
    """Parse duration from MP4 mvhd box. No external tools required."""
    with mp4.open("rb") as f:
        data = f.read()

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
                raise ValueError(f"mvhd timescale is zero in {mp4}")
            return duration / timescale

        pos += box_size

    raise ValueError(f"No mvhd box found in {mp4}")


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

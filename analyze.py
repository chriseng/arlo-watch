"""Analyze downloaded Arlo clips with Gemini and write JSON summaries."""

import json
import logging
import os
import time
from pathlib import Path

import google.generativeai as genai
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

PROMPT = """Analyze this security camera clip and return ONLY a valid JSON object with these fields:
- duration_seconds (integer: estimated clip length)
- persons (integer: number of distinct people visible)
- vehicles (integer: number of distinct vehicles visible)
- animals (integer: number of distinct animals visible)
- activity (string: one sentence describing what happens in the clip)
- notable_events (array of strings: specific actions, e.g. "person approached door")
- motion_area (string: where primary motion occurs, e.g. "left", "center", "right", "full frame")
- time_of_day (string: one of "day", "dusk", "night", "dawn")
- confidence (string: one of "high", "medium", "low" — your confidence in the analysis)

Return only the JSON object. No markdown fences, no explanation, no extra text."""

POLL_INTERVAL_SECONDS = 5
UPLOAD_TIMEOUT_SECONDS = 300


def upload_and_wait(path: Path) -> genai.types.File:
    log.info("Uploading %s to Gemini Files API...", path.name)
    video_file = genai.upload_file(path=str(path), mime_type="video/mp4")

    elapsed = 0
    while video_file.state.name == "PROCESSING":
        if elapsed >= UPLOAD_TIMEOUT_SECONDS:
            raise RuntimeError(
                f"Timed out waiting for Gemini to process {path.name}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
        video_file = genai.get_file(video_file.name)

    if video_file.state.name != "ACTIVE":
        raise RuntimeError(
            f"Gemini file processing failed for {path.name}: state={video_file.state.name}"
        )
    return video_file


def analyze_clip(path: Path) -> dict:
    video_file = upload_and_wait(path)
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content([video_file, PROMPT])
        raw = response.text.strip()
        return json.loads(raw)
    finally:
        try:
            genai.delete_file(video_file.name)
        except Exception as e:
            log.warning("Could not delete Gemini file %s: %s", video_file.name, e)


def main() -> None:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    clips = sorted(CLIPS_DIR.glob("*.mp4"))
    pending = [c for c in clips if not c.with_suffix(".json").exists()]
    log.info("%d clip(s) pending analysis.", len(pending))

    succeeded = 0
    failed = 0
    for clip in pending:
        out = clip.with_suffix(".json")
        log.info("Analyzing %s...", clip.name)
        try:
            result = analyze_clip(clip)
            result["clip_file"] = clip.name
            out.write_text(json.dumps(result, indent=2))
            log.info("Saved %s", out.name)
            succeeded += 1
        except Exception as e:
            log.error("Failed to analyze %s: %s", clip.name, e)
            failed += 1

    log.info("Analysis complete: %d succeeded, %d failed.", succeeded, failed)


if __name__ == "__main__":
    main()

"""Analyze downloaded Arlo clips with Gemini and write JSON summaries."""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
from google import genai
from google.genai import types
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
EASTERN_TZ = ZoneInfo("America/New_York")

PROMPT = """Analyze this security camera clip and return ONLY a valid JSON object with these fields:
- duration_seconds (integer: estimated clip length)
- persons (integer: number of distinct people visible)
- vehicles (integer: number of distinct vehicles visible)
- animals (integer: number of distinct animals visible)
- activity (string: one sentence describing what happens in the clip, up to a paragraph if necessary. identify species of birds when able)
- notable_events (array of strings: specific actions, e.g. "person approached door")
- motion_area (string: where primary motion occurs, e.g. "left", "center", "right", "full frame")
- time_of_day (string: one of "day", "dusk", "night", "dawn")
- confidence (string: one of "high", "medium", "low" — your confidence in the analysis)
- screenshot_timestamp_seconds (number: best timestamp for a single representative screenshot from this clip)
- screenshot_reason (string: brief explanation of why that frame best represents the clip)

Return only the JSON object. No markdown fences, no explanation, no extra text."""

POLL_INTERVAL_SECONDS = 5
UPLOAD_TIMEOUT_SECONDS = 300
GENERATE_RETRIES = 3


def is_retryable_generate_error(error: Exception) -> bool:
    message = str(error)
    return "503 UNAVAILABLE" in message or "429" in message


def upload_and_wait(client: genai.Client, path: Path):
    log.info("Uploading %s to Gemini Files API...", path.name)
    video_file = client.files.upload(file=str(path))

    elapsed = 0
    while video_file.state.name == "PROCESSING":
        if elapsed >= UPLOAD_TIMEOUT_SECONDS:
            raise RuntimeError(
                f"Timed out waiting for Gemini to process {path.name}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
        video_file = client.files.get(name=video_file.name)

    if video_file.state.name != "ACTIVE":
        raise RuntimeError(
            f"Gemini file processing failed for {path.name}: state={video_file.state.name}"
        )
    return video_file


def analyze_clip(client: genai.Client, path: Path) -> dict:
    video_file = upload_and_wait(client, path)
    try:
        for attempt in range(1, GENERATE_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[video_file, PROMPT],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )
                return parse_json_response(response, path.name)
            except Exception as e:
                if attempt == GENERATE_RETRIES or not is_retryable_generate_error(e):
                    raise
                delay = attempt * 5
                log.warning(
                    "Retrying Gemini analysis for %s after attempt %d/%d failed: %s",
                    path.name,
                    attempt,
                    GENERATE_RETRIES,
                    e,
                )
                time.sleep(delay)
    finally:
        try:
            client.files.delete(name=video_file.name)
        except Exception as e:
            log.warning("Could not delete Gemini file %s: %s", video_file.name, e)


def parse_json_response(response, clip_name: str) -> dict:
    candidates = []

    text = getattr(response, "text", None)
    if text:
        candidates.append(text)

    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                candidates.append(part_text)

    for raw in candidates:
        cleaned = raw.strip()
        if not cleaned:
            continue
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group(0))

    finish_reasons = [
        str(getattr(candidate, "finish_reason", "unknown"))
        for candidate in getattr(response, "candidates", []) or []
    ]
    preview = " | ".join(candidate.strip().replace("\n", " ")[:200] for candidate in candidates if candidate.strip())
    raise RuntimeError(
        f"Gemini returned no parseable JSON for {clip_name}; "
        f"finish_reasons={finish_reasons or ['none']}; preview={preview or '<empty>'}"
    )


def clip_timestamp_est(path: Path) -> str:
    stem = path.stem
    if stem.endswith("_UTC"):
        utc_dt = datetime.strptime(stem, "%Y%m%d_%H%M%S_UTC").replace(
            tzinfo=timezone.utc
        )
        return utc_dt.astimezone(EASTERN_TZ).isoformat()
    if stem.endswith("_EST") or stem.endswith("_EDT"):
        local_dt = datetime.strptime(stem[:-4], "%Y%m%d_%H%M%S").replace(
            tzinfo=EASTERN_TZ
        )
        return local_dt.isoformat()
    raise ValueError(
        f"Unsupported clip filename format for timestamp extraction: {path.name}"
    )


def screenshot_filename(path: Path) -> str:
    return path.with_suffix(".jpg").name


def extract_screenshot(clip: Path, timestamp_seconds: float, dest: Path) -> None:
    capture = cv2.VideoCapture(str(clip))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video for screenshot extraction: {clip}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration_seconds = frame_count / fps if fps > 0 and frame_count > 0 else None
        seconds = max(0.0, float(timestamp_seconds))
        if duration_seconds is not None:
            seconds = min(seconds, max(duration_seconds - 0.1, 0.0))

        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = capture.read()
        if not ok:
            if fps > 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(int(seconds * fps), 0))
                ok, frame = capture.read()
        if not ok:
            raise RuntimeError(
                f"Could not decode screenshot frame at {seconds:.2f}s for {clip.name}"
            )

        if not cv2.imwrite(str(dest), frame):
            raise RuntimeError(f"Could not write screenshot file: {dest}")
    finally:
        capture.release()


def clip_needs_analysis(clip: Path) -> bool:
    json_path = clip.with_suffix(".json")
    if not json_path.exists():
        return True
    try:
        data = json.loads(json_path.read_text())
    except Exception:
        return True
    required = [
        "clip_file",
        "timestamp_est",
        "screenshot_timestamp_seconds",
        "screenshot_reason",
        "screenshot_file",
    ]
    if any(key not in data for key in required):
        return True
    screenshot_path = clip.with_suffix(".jpg")
    return not screenshot_path.exists()


def main() -> None:
    with genai.Client(api_key=os.environ["GEMINI_API_KEY"]) as client:
        clips = sorted(CLIPS_DIR.glob("*.mp4"))
        pending = [c for c in clips if clip_needs_analysis(c)]
        log.info("%d clip(s) pending analysis.", len(pending))

        succeeded = 0
        failed = 0
        for clip in pending:
            out = clip.with_suffix(".json")
            log.info("Analyzing %s...", clip.name)
            try:
                result = analyze_clip(client, clip)
                result["clip_file"] = clip.name
                result["timestamp_est"] = clip_timestamp_est(clip)
                screenshot_seconds = float(result.get("screenshot_timestamp_seconds", 0))
                screenshot_path = clip.with_suffix(".jpg")
                extract_screenshot(clip, screenshot_seconds, screenshot_path)
                result["screenshot_timestamp_seconds"] = screenshot_seconds
                result["screenshot_file"] = screenshot_filename(clip)
                out.write_text(json.dumps(result, indent=2))
                log.info("Saved %s", out.name)
                succeeded += 1
            except Exception as e:
                log.error("Failed to analyze %s: %s", clip.name, e)
                failed += 1

        log.info("Analysis complete: %d succeeded, %d failed.", succeeded, failed)


if __name__ == "__main__":
    main()

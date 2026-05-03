"""Analyze downloaded Arlo clips with Gemini and write JSON summaries."""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
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
STRIP_AUDIO_BEFORE_UPLOAD = os.getenv("STRIP_AUDIO_BEFORE_UPLOAD", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
EASTERN_TZ = ZoneInfo("America/New_York")

PROMPT = """Analyze this security camera clip and return ONLY a valid JSON object with these fields:
- duration_seconds (integer: estimated clip length)
- persons (integer: number of distinct people visible)
- vehicles (integer: number of distinct vehicles visible)
- animals (integer: number of distinct animals visible)
- activity (string: one sentence describing what happens in the clip, up to a paragraph if necessary. once an animal is clearly visible, prefer the most specific visually supported label, using qualifiers such as "likely" when needed)
- notable_events (array of strings: specific actions, e.g. "person approached door")
- motion_area (string: where primary motion occurs, e.g. "left", "center", "right", "full frame")
- time_of_day (string: one of "day", "dusk", "night", "dawn")
- confidence (string: one of "high", "medium", "low" — your confidence in the analysis)
- screenshot_timestamp_seconds (number: best timestamp for a single representative screenshot from this clip)
- evidence_timestamps_seconds (array of 1-3 numbers: timestamps where the clearest visible subjects appear; if no subject is clearly visible, return an empty array)
- screenshot_reason (string: brief explanation of why that frame best represents the clip)

Core evidence rules:
- Report only what is directly visible in the clip. Treat the clip as self-contained and ignore prior expectations, common scene patterns, or likely trigger causes.
- A subject counts only if some part of it is actually visible in one or more frames. Do not infer a subject from motion alone, shadows, ripples, rustling foliage, off-frame sounds, scene context, or the fact that the camera recorded a clip.
- Do not assume an animal, person, or vehicle entered or exited the frame unless that subject is visible.
- If no clearly identifiable person, vehicle, or animal is visible, set persons=0, vehicles=0, animals=0 and describe the clip conservatively as empty or ambiguous motion.
- When evidence is weak, prefer "unknown animal", "indistinct person", or "no clearly identifiable subject visible" over a specific claim.

Animal identification rules:
- First decide whether an animal is actually visible. Once an animal is clearly visible, try aggressively to identify bird species from visual traits in the clip.
- If one species is the clearly best fit from the visible traits, use the species name directly without "likely".
- Use qualifiers such as "likely" or "possibly" only when there are multiple plausible species and the clip does not clearly distinguish between them.
- Try to identify other animals as specifically as the visible evidence supports. If the animal cannot be identified with reasonable confidence, use a broader label such as "small mammal", "cat", "dog", "deer", "raccoon-like animal", or "unknown animal".
- Base identification only on directly visible traits in the clip, such as size, silhouette, movement, tail shape, ear shape, wings, beak, markings, or antlers. Do not infer species from location, typical neighborhood wildlife, or prior probability.
- For night or infrared footage, lower your species confidence substantially. Do not rely on color, fine texture, or markings that are not clearly visible in grayscale IR footage.
- In night or infrared clips, rely more on body proportions, gait, posture, tail thickness and length, snout shape, and ear shape. If those cues are not clear, use a broader label instead of a precise species.
- In night or infrared clips, use a species name only when at least two independent visible traits support that identification. If the guess depends mainly on a single ambiguous cue such as overall size or silhouette alone, use a broader label instead.
- In night or infrared clips, species-level mammal identification should be uncommon. When in doubt, prefer shape-based labels such as "small mammal", "rabbit-sized mammal", "long-tailed mammal", "stocky mammal", or "unknown mammal" over a specific species.
- Count only distinct animals that are actually visible. If repeated appearances may be the same animal, prefer the lower count unless multiple animals are clearly present at once.
- In the activity field, if an animal is clearly visible, prefer the most specific visually supported label. Do not add "likely" by default, and do not use it when one species is the clear best match.
- Sometimes there will not be any animals in the video, especially when people are present. Be sure that animals are actually in the video before claiming that they are.

Reasoning order:
1. First decide whether any person, vehicle, or animal is clearly visible at all. If not, return zero counts and an empty-scene or ambiguous-motion description.
2. If subjects are visible, count distinct visible subjects conservatively.
3. If an animal is visible, identify it to the most specific visually supported level and use a qualified species guess when appropriate.
4. Write the activity description conservatively and factually, describing uncertainty plainly rather than filling gaps.

Return only the JSON object. No markdown fences, no explanation, no extra text."""

FRAME_VERIFICATION_PROMPT = """You are verifying still frames extracted from a security camera clip.
You may receive multiple labeled contact sheets for the same clip, including a full-scene sheet and a zoomed-in sheet using the same timestamps.

Return ONLY a valid JSON object with these fields:
- persons (integer: number of distinct people clearly visible across the provided frames)
- vehicles (integer: number of distinct vehicles clearly visible across the provided frames)
- animals (integer: number of distinct animals clearly visible across the provided frames)
- activity_sample_frames (array of strings: grid labels such as "A1", "B3", "D2", or "F1" where a clearly visible subject appears; leave empty if none)
- visible_subjects (array of short strings describing only subjects clearly visible in at least one frame)
- frame_assessment (string: one sentence stating whether the frames show a clearly visible subject or only ambiguous/background motion)
- confidence (string: one of "high", "medium", "low")

Rules:
- Count only subjects that are clearly visible in the provided frames.
- Cite only grid cells where the subject itself is visible, not where you infer it from surrounding context.
- Do not infer a subject from scene context, motion blur, shadows, water movement, foliage movement, or likely trigger causes.
- If no clearly identifiable person, vehicle, or animal is visible in any frame, return persons=0, vehicles=0, animals=0 and state that no clearly visible subject is present.
- Prefer a false negative over a false positive.

Return only the JSON object. No markdown fences, no explanation, no extra text."""

POLL_INTERVAL_SECONDS = 5
UPLOAD_TIMEOUT_SECONDS = 300
GENERATE_RETRIES = 3


def strip_audio_for_upload(path: Path) -> Path:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError(
            "STRIP_AUDIO_BEFORE_UPLOAD is enabled, but ffmpeg is not installed or not on PATH"
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="arlo-watch-upload-"))
    stripped_path = temp_dir / f"{path.stem}.noaudio.mp4"
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(path),
        "-an",
        "-c:v",
        "copy",
        str(stripped_path),
    ]
    log.info("Stripping audio from %s before upload...", path.name)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        message = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(
            f"ffmpeg failed while stripping audio from {path.name}: {message or e}"
        ) from e
    return stripped_path


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
    upload_path = path
    temp_dir = None
    video_file = None
    if STRIP_AUDIO_BEFORE_UPLOAD:
        upload_path = strip_audio_for_upload(path)
        temp_dir = upload_path.parent

    try:
        video_file = upload_and_wait(client, upload_path)
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
                result = parse_json_response(response, path.name)
                return verify_clip_result(client, path, result)
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
        if video_file is not None:
            try:
                client.files.delete(name=video_file.name)
            except Exception as e:
                log.warning("Could not delete Gemini file %s: %s", video_file.name, e)
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


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


def verification_sheet_path(clip: Path, variant: str = "wide") -> Path:
    return clip.with_suffix(f".verify-{variant}-sheet.jpg")


def normalized_evidence_timestamps(result: dict, fallback_seconds: float) -> list[float]:
    raw = result.get("evidence_timestamps_seconds")
    timestamps = []
    if isinstance(raw, list):
        for value in raw:
            try:
                timestamps.append(float(value))
            except (TypeError, ValueError):
                continue

    if not timestamps:
        timestamps = [fallback_seconds]

    deduped = []
    seen = set()
    for ts in timestamps:
        rounded = round(ts, 2)
        if rounded in seen:
            continue
        seen.add(rounded)
        deduped.append(ts)
        if len(deduped) == 3:
            break
    return deduped


def clip_duration_seconds(clip: Path) -> float | None:
    capture = cv2.VideoCapture(str(clip))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video for duration detection: {clip}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if fps > 0 and frame_count > 0:
            return frame_count / fps
        return None
    finally:
        capture.release()


def build_verification_timestamps(clip: Path, result: dict) -> list[float]:
    screenshot_seconds = float(result.get("screenshot_timestamp_seconds", 0))
    duration_seconds = clip_duration_seconds(clip)
    evidence_timestamps = normalized_evidence_timestamps(result, screenshot_seconds)

    timestamps: list[float] = []
    fallback_timestamps: list[float] = []
    evidence_count = len(evidence_timestamps)

    if evidence_count >= 3:
        for ts in evidence_timestamps[:3]:
            timestamps.extend([ts - 0.4, ts, ts + 0.4])
            fallback_timestamps.extend([ts + 0.8, ts + 1.2, ts + 1.6])
    elif evidence_count == 2:
        for ts in evidence_timestamps:
            timestamps.extend([ts - 0.6, ts - 0.2, ts + 0.2, ts + 0.6])
            fallback_timestamps.extend([ts + 1.0, ts + 1.4, ts + 1.8])
        if duration_seconds and duration_seconds > 0:
            timestamps.extend([duration_seconds * 0.2, duration_seconds * 0.8])
    elif evidence_count == 1:
        ts = evidence_timestamps[0]
        timestamps.extend([
            ts - 1.0,
            ts - 0.6,
            ts - 0.3,
            ts - 0.1,
            ts,
            ts + 0.1,
            ts + 0.3,
            ts + 0.6,
            ts + 1.0,
        ])
        fallback_timestamps.extend([ts + 1.4, ts + 1.8, ts + 2.2, ts + 2.8, ts + 3.4])
    elif duration_seconds and duration_seconds > 0:
        sample_points = [0.05, 0.15, 0.25, 0.4, 0.55, 0.7, 0.82, 0.92, 0.98]
        for ratio in sample_points:
            timestamps.append(duration_seconds * ratio)
    else:
        timestamps = [screenshot_seconds]

    if evidence_count > 0:
        timestamps.extend(fallback_timestamps)
        if duration_seconds and duration_seconds > 0:
            timestamps.extend([
                duration_seconds * 0.15,
                duration_seconds * 0.35,
                duration_seconds * 0.55,
                duration_seconds * 0.75,
                duration_seconds * 0.9,
            ])
        else:
            timestamps.extend([screenshot_seconds + 1.5, screenshot_seconds + 3.0, screenshot_seconds + 4.5])

    deduped = []
    seen = set()
    for ts in timestamps:
        rounded = round(max(ts, 0.0), 1)
        if rounded in seen:
            continue
        seen.add(rounded)
        deduped.append(rounded)
        if len(deduped) == 9:
            break
    return deduped


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


def crop_verification_focus(frame, motion_area: str) -> any:
    height, width = frame.shape[:2]
    crop_width = max(int(width * 0.5), 1)
    crop_height = max(int(height * 0.5), 1)

    area = motion_area.strip().lower()
    center_x = width // 2
    center_y = height // 2

    if "left" in area:
        center_x = width // 4
    elif "right" in area:
        center_x = (width * 3) // 4

    if "top" in area or "upper" in area:
        center_y = height // 4
    elif "bottom" in area or "lower" in area:
        center_y = (height * 3) // 4

    half_width = crop_width // 2
    half_height = crop_height // 2
    start_x = min(max(center_x - half_width, 0), max(width - crop_width, 0))
    start_y = min(max(center_y - half_height, 0), max(height - crop_height, 0))
    end_x = min(start_x + crop_width, width)
    end_y = min(start_y + crop_height, height)
    return frame[start_y:end_y, start_x:end_x]


def build_verification_sheet(
    clip: Path,
    timestamps: list[float],
    dest: Path,
    labels: list[str],
    zoomed: bool = False,
    motion_area: str = "",
) -> None:
    capture = cv2.VideoCapture(str(clip))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video for verification sheet: {clip}")

    tile_width = 960
    tile_height = 540
    label_width = 320
    label_height = 64
    frames = []
    try:
        for index, seconds in enumerate(timestamps):
            capture.set(cv2.CAP_PROP_POS_MSEC, float(seconds) * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            if zoomed:
                frame = crop_verification_focus(frame, motion_area)
            tile = cv2.resize(frame, (tile_width, tile_height))
            label = labels[index] if index < len(labels) else f"F{index + 1}"
            cv2.rectangle(tile, (0, 0), (label_width, label_height), (0, 0, 0), -1)
            cv2.putText(
                tile,
                f"{label}  {seconds:.1f}s",
                (18, 42),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.25,
                (255, 255, 255),
                3,
                cv2.LINE_AA,
            )
            frames.append(tile)
    finally:
        capture.release()

    if not frames:
        raise RuntimeError(f"Could not extract any frames for verification sheet: {clip.name}")

    while len(frames) < 9:
        frames.append(frames[-1].copy())

    rows = []
    for row_index in range(0, 9, 3):
        rows.append(cv2.hconcat(frames[row_index:row_index + 3]))
    sheet = cv2.vconcat(rows)
    if not cv2.imwrite(str(dest), sheet):
        raise RuntimeError(f"Could not write verification sheet: {dest}")


def verification_frame_labels() -> list[str]:
    return [
        "A1", "A2", "A3",
        "B1", "B2", "B3",
        "C1", "C2", "C3",
    ]


def verification_zoom_frame_labels() -> list[str]:
    return [
        "D1", "D2", "D3",
        "E1", "E2", "E3",
        "F1", "F2", "F3",
    ]


def verification_label_timestamps(timestamps: list[float]) -> dict[str, float]:
    labels = verification_frame_labels() + verification_zoom_frame_labels()
    return {
        label: round(float(timestamp), 1)
        for label, timestamp in zip(labels, timestamps + timestamps)
    }


def upload_files_and_wait(client: genai.Client, paths: list[Path]) -> list:
    uploaded = []
    for path in paths:
        uploaded.append(upload_and_wait(client, path))
    return uploaded


def delete_uploaded_files(client: genai.Client, files: list) -> None:
    for uploaded_file in files:
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception as e:
            log.warning("Could not delete Gemini file %s: %s", uploaded_file.name, e)


def verify_clip_result(client: genai.Client, clip: Path, result: dict) -> dict:
    claimed_subjects = sum(int(result.get(key, 0) or 0) for key in ("persons", "vehicles", "animals"))
    claimed_animals = int(result.get("animals", 0) or 0)
    if claimed_animals <= 0:
        return result

    timestamps = build_verification_timestamps(clip, result)
    label_timestamps = verification_label_timestamps(timestamps)
    motion_area = str(result.get("motion_area", "") or "")
    wide_sheet_path = verification_sheet_path(clip, "wide")
    zoom_sheet_path = verification_sheet_path(clip, "zoom")
    build_verification_sheet(
        clip,
        timestamps,
        wide_sheet_path,
        verification_frame_labels(),
    )
    build_verification_sheet(
        clip,
        timestamps,
        zoom_sheet_path,
        verification_zoom_frame_labels(),
        zoomed=True,
        motion_area=motion_area,
    )

    uploaded_frames = []
    try:
        uploaded_frames = upload_files_and_wait(client, [wide_sheet_path, zoom_sheet_path])
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=uploaded_frames + [FRAME_VERIFICATION_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        verification = parse_json_response(response, f"frame verification for {clip.name}")
    finally:
        if uploaded_frames:
            delete_uploaded_files(client, uploaded_frames)
        wide_sheet_path.unlink(missing_ok=True)
        zoom_sheet_path.unlink(missing_ok=True)

    verified_subjects = sum(
        int(verification.get(key, 0) or 0) for key in ("persons", "vehicles", "animals")
    )
    activity_sample_frames = verification.get("activity_sample_frames")
    if not isinstance(activity_sample_frames, list):
        activity_sample_frames = verification.get("animal_frames", [])
    if not isinstance(activity_sample_frames, list):
        activity_sample_frames = []

    sample_frame_timestamps = {
        label: label_timestamps[label]
        for label in activity_sample_frames
        if isinstance(label, str) and label in label_timestamps
    }
    verified_animals = int(verification.get("animals", 0) or 0)
    verified_animals_reliable = verified_animals > 0 and len(activity_sample_frames) >= 1

    if claimed_subjects > 0 and (verified_subjects == 0 or not verified_animals_reliable):
        result["verification"] = {
            "presence_conflict": True,
            "frame_assessment": verification.get("frame_assessment"),
            "activity_sample_frames": activity_sample_frames,
            "activity_sample_frame_timestamps_seconds": sample_frame_timestamps,
            "verification_sample_timestamps_seconds": label_timestamps,
            "visible_subjects": verification.get("visible_subjects", []),
            "confidence": verification.get("confidence"),
        }
    else:
        result["verification"] = {
            "presence_conflict": False,
            "frame_assessment": verification.get("frame_assessment"),
            "activity_sample_frames": activity_sample_frames,
            "activity_sample_frame_timestamps_seconds": sample_frame_timestamps,
            "verification_sample_timestamps_seconds": label_timestamps,
            "visible_subjects": verification.get("visible_subjects", []),
            "confidence": verification.get("confidence"),
        }

    return result


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

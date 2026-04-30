"""Build a single-file HTML gallery from analyzed Arlo clips."""

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


load_dotenv()

CLIPS_DIR = Path(os.getenv("CLIPS_DIR", "html/clips"))
HTML_DIR = Path("html")
OUTPUT_PATH = HTML_DIR / "index.html"
SUMMARY_CACHE_PATH = HTML_DIR / "day_summaries.json"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
GENERATE_RETRIES = 3

DAY_SUMMARY_PROMPT = """You are summarizing a single day of security camera activity.

Given the JSON records for all clips from one day, return ONLY a valid JSON object with:
- headline: a short one-line title for the day
- summary: 2-4 sentences summarizing the day
- highlights: an array of short bullet-like strings covering the most notable moments or patterns

Focus on the whole day, not a single clip. Mention recurring animals, people, time clusters, and any unusual activity if present.
Do not mention missing data or speculate beyond what is in the JSON records.
Return only the JSON object."""


def relative_href(path: Path) -> str:
    return os.path.relpath(path, OUTPUT_PATH.parent).replace(os.sep, "/")


def parse_json_response(response, context: str) -> dict:
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

    raise RuntimeError(f"Gemini returned no parseable JSON for {context}")


def is_retryable_generate_error(error: Exception) -> bool:
    message = str(error)
    return "503 UNAVAILABLE" in message or "429" in message


def summarize_day(client: genai.Client, day: str, records: list[dict]) -> dict:
    payload = json.dumps(records, indent=2)
    for attempt in range(1, GENERATE_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    f"Day: {day}\n\nClip JSON records:\n{payload}",
                    DAY_SUMMARY_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )
            return parse_json_response(response, f"day summary for {day}")
        except Exception as e:
            if attempt == GENERATE_RETRIES or not is_retryable_generate_error(e):
                raise
            time.sleep(attempt * 5)
    raise RuntimeError(f"Could not summarize {day}")


def load_summary_cache() -> dict:
    if not SUMMARY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(SUMMARY_CACHE_PATH.read_text())
    except Exception:
        return {}


def save_summary_cache(cache: dict) -> None:
    HTML_DIR.mkdir(exist_ok=True)
    SUMMARY_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def day_digest(records: list[dict]) -> str:
    material = json.dumps(records, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_entries() -> list[dict]:
    entries = []
    for json_path in sorted(CLIPS_DIR.glob("*.json")):
        data = json.loads(json_path.read_text())
        clip_file = data.get("clip_file")
        timestamp_est = data.get("timestamp_est")
        if not clip_file or not timestamp_est:
            continue
        dt = datetime.fromisoformat(timestamp_est)
        entries.append(
            {
                "day": dt.date().isoformat(),
                "time": dt.strftime("%I:%M:%S %p").lstrip("0"),
                "timestamp_est": timestamp_est,
                "duration_seconds": data.get("duration_seconds"),
                "clip_file": clip_file,
                "clip_href": relative_href(CLIPS_DIR / clip_file),
                "screenshot_file": data.get("screenshot_file"),
                "screenshot_href": relative_href(CLIPS_DIR / data["screenshot_file"])
                if data.get("screenshot_file")
                else None,
                "json": data,
            }
        )
    entries.sort(key=lambda entry: entry["timestamp_est"], reverse=True)
    return entries


def build_day_summaries(entries: list[dict]) -> dict:
    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry["day"]].append(entry["json"])

    latest_day = max(grouped) if grouped else None
    cache = load_summary_cache()
    updated_cache = dict(cache)
    summaries = {}

    with genai.Client(api_key=os.environ["GEMINI_API_KEY"]) as client:
        for day, records in grouped.items():
            digest = day_digest(records)
            cached = cache.get(day)

            # Historical days are immutable once summarized. Only the most recent
            # day remains eligible for refresh as new clips arrive.
            if day != latest_day and cached and cached.get("summary"):
                summaries[day] = cached["summary"]
                continue

            if cached and cached.get("digest") == digest and cached.get("summary"):
                summaries[day] = cached["summary"]
                continue

            summary = summarize_day(client, day, records)
            updated_cache[day] = {"digest": digest, "summary": summary}
            summaries[day] = summary

    if updated_cache != cache:
        save_summary_cache(updated_cache)
    return summaries


def build_html(entries: list[dict], day_summaries: dict) -> str:
    data_json = json.dumps(entries)
    summary_json = json.dumps(day_summaries)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Arlo Watch Gallery</title>
  <style>
    :root {{
      --bg: #f4efe4;
      --panel: #fffaf0;
      --ink: #1b1b18;
      --muted: #6a675d;
      --accent: #2f6f57;
      --line: #d8cfbf;
      --shadow: rgba(34, 28, 14, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(216,179,106,.22), transparent 28%),
        linear-gradient(180deg, #f8f3e8 0%, var(--bg) 100%);
    }}
    .shell {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3.5rem);
      line-height: 1;
    }}
    .sub {{
      margin: 0 0 24px;
      color: var(--muted);
      font-size: 1rem;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
      padding: 16px;
      border: 1px solid var(--line);
      background: rgba(255,250,240,.85);
      backdrop-filter: blur(10px);
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    .toolbar label {{
      font-size: .85rem;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: var(--muted);
    }}
    input[type="date"] {{
      padding: 10px 12px;
      border: 1px solid var(--line);
      background: white;
      font: inherit;
    }}
    button {{
      padding: 10px 14px;
      border: 0;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font: inherit;
    }}
    .summary-meta {{
      margin-left: auto;
      color: var(--muted);
      font-size: .95rem;
    }}
    .day-summary {{
      margin-top: 22px;
      padding: 22px;
      border: 1px solid var(--line);
      background: rgba(255,250,240,.9);
      box-shadow: 0 14px 40px var(--shadow);
    }}
    .day-summary h2 {{
      margin: 0 0 10px;
      font-size: 1.4rem;
    }}
    .day-summary p {{
      margin: 0 0 14px;
      line-height: 1.55;
    }}
    .day-summary ul {{
      margin: 0;
      padding-left: 20px;
    }}
    .rows {{
      display: grid;
      gap: 16px;
      margin-top: 18px;
    }}
    .row {{
      display: grid;
      grid-template-columns: minmax(420px, 1.4fr) minmax(260px, .9fr) minmax(260px, .9fr);
      gap: 0;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 14px 40px var(--shadow);
      overflow: hidden;
    }}
    .cell {{
      padding: 16px;
      border-left: 1px solid var(--line);
    }}
    .cell:first-child {{
      border-left: 0;
    }}
    video {{
      width: 100%;
      aspect-ratio: 16 / 9;
      display: block;
      background: #181512;
    }}
    .stamp {{
      margin-top: 10px;
      color: var(--muted);
      font-size: .95rem;
    }}
    .media-links {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    .media-links a {{
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px solid rgba(47,111,87,.3);
    }}
    .cell h3 {{
      margin: 0 0 10px;
      font-size: .82rem;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: var(--muted);
    }}
    .activity {{
      margin: 0;
      line-height: 1.55;
    }}
    .events {{
      margin: 0;
      padding-left: 18px;
      line-height: 1.5;
    }}
    .events li + li {{
      margin-top: 6px;
    }}
    .empty {{
      padding: 28px;
      border: 1px dashed var(--line);
      color: var(--muted);
      margin-top: 22px;
      background: rgba(255,255,255,.5);
    }}
    @media (max-width: 980px) {{
      .row {{
        grid-template-columns: 1fr;
      }}
      .cell {{
        border-left: 0;
        border-top: 1px solid var(--line);
      }}
      .cell:first-child {{
        border-top: 0;
      }}
      .summary-meta {{
        width: 100%;
        margin-left: 0;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <h1>Arlo Watch</h1>
    <p class="sub">Per-video summaries with Gemini-selected screenshots and day summaries.</p>
    <div class="toolbar">
      <label for="dayPicker">Day</label>
      <input id="dayPicker" type="date">
      <button id="showLatest" type="button">Latest Day</button>
      <div id="summaryMeta" class="summary-meta"></div>
    </div>
    <div id="content"></div>
  </div>
  <script>
    const entries = {data_json};
    const daySummaries = {summary_json};
    const dayPicker = document.getElementById('dayPicker');
    const showLatest = document.getElementById('showLatest');
    const content = document.getElementById('content');
    const summaryMeta = document.getElementById('summaryMeta');
    const days = [...new Set(entries.map((entry) => entry.day))].sort().reverse();

    function render(day) {{
      const filtered = entries.filter((entry) => entry.day === day);
      summaryMeta.textContent = filtered.length ? `${{filtered.length}} clip(s) on ${{day}}` : `0 clip(s) on ${{day}}`;
      if (!filtered.length) {{
        content.innerHTML = '<div class="empty">No clips for the selected day.</div>';
        return;
      }}

      const summary = daySummaries[day];
      const summaryHtml = summary ? `
        <section class="day-summary">
          <h2>${{summary.headline || day}}</h2>
          <p>${{summary.summary || ''}}</p>
          ${{summary.highlights?.length ? `<ul>${{summary.highlights.map((item) => `<li>${{item}}</li>`).join('')}}</ul>` : ''}}
        </section>
      ` : '';

      const rows = filtered.map((entry) => {{
        const events = Array.isArray(entry.json.notable_events) ? entry.json.notable_events : [];
        const duration = entry.duration_seconds != null ? ` (${{entry.duration_seconds}}s)` : '';
        const poster = entry.screenshot_href ? ` poster="${{entry.screenshot_href}}"` : '';
        return `
          <article class="row">
            <section class="cell">
              <video controls preload="metadata"${{poster}}>
                <source src="${{entry.clip_href}}" type="video/mp4">
              </video>
              <div class="stamp">${{entry.time}}${{duration}}</div>
              <div class="media-links">
                <a href="${{entry.clip_href}}" target="_blank" rel="noreferrer">Open clip</a>
                ${{entry.screenshot_href ? `<a href="${{entry.screenshot_href}}" target="_blank" rel="noreferrer">Open screenshot</a>` : ''}}
              </div>
            </section>
            <section class="cell">
              <h3>Activity</h3>
              <p class="activity">${{entry.json.activity || ''}}</p>
            </section>
            <section class="cell">
              <h3>Notable Events</h3>
              ${{events.length ? `<ul class="events">${{events.map((item) => `<li>${{item}}</li>`).join('')}}</ul>` : '<p class="activity">None recorded.</p>'}}
            </section>
          </article>
        `;
      }}).join('');

      content.innerHTML = `${{summaryHtml}}<div class="rows">${{rows}}</div>`;
    }}

    if (days.length) {{
      dayPicker.min = days[days.length - 1];
      dayPicker.max = days[0];
      dayPicker.value = days[0];
      render(days[0]);
    }} else {{
      content.innerHTML = '<div class="empty">No analyzed clips found.</div>';
    }}

    dayPicker.addEventListener('change', () => render(dayPicker.value));
    showLatest.addEventListener('click', () => {{
      if (!days.length) return;
      dayPicker.value = days[0];
      render(days[0]);
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    HTML_DIR.mkdir(exist_ok=True)
    entries = load_entries()
    day_summaries = build_day_summaries(entries) if entries else {}
    OUTPUT_PATH.write_text(build_html(entries, day_summaries))
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()

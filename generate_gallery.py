"""Build a single-file HTML gallery from analyzed Arlo clips."""

import json
from datetime import datetime
from pathlib import Path


CLIPS_DIR = Path("clips")
HTML_DIR = Path("html")
OUTPUT_PATH = HTML_DIR / "index.html"


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
                "clip_file": clip_file,
                "clip_href": f"../clips/{clip_file}",
                "screenshot_file": data.get("screenshot_file"),
                "screenshot_href": f"../clips/{data['screenshot_file']}"
                if data.get("screenshot_file")
                else None,
                "json": data,
            }
        )
    entries.sort(key=lambda entry: entry["timestamp_est"], reverse=True)
    return entries


def build_html(entries: list[dict]) -> str:
    data_json = json.dumps(entries)
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
      --accent-2: #d8b36a;
      --line: #d8cfbf;
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
      max-width: 1200px;
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
    .summary {{
      margin-left: auto;
      color: var(--muted);
      font-size: .95rem;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
      margin-top: 22px;
    }}
    .card {{
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 14px 40px rgba(34, 28, 14, .08);
      overflow: hidden;
    }}
    .shot {{
      aspect-ratio: 16 / 9;
      width: 100%;
      object-fit: cover;
      display: block;
      background: #ddd3c4;
    }}
    .body {{
      padding: 16px;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: .8rem;
      text-transform: uppercase;
      letter-spacing: .08em;
      margin-bottom: 6px;
    }}
    .title {{
      margin: 0 0 10px;
      font-size: 1.15rem;
    }}
    .links {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .links a {{
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px solid rgba(47,111,87,.3);
    }}
    pre {{
      margin: 0;
      padding: 12px;
      background: #201d18;
      color: #f8f2de;
      overflow: auto;
      font-size: .82rem;
      line-height: 1.45;
    }}
    .empty {{
      padding: 28px;
      border: 1px dashed var(--line);
      color: var(--muted);
      margin-top: 22px;
      background: rgba(255,255,255,.5);
    }}
  </style>
</head>
<body>
  <div class="shell">
    <h1>Arlo Watch</h1>
    <p class="sub">Per-video summaries with Gemini-selected screenshots.</p>
    <div class="toolbar">
      <label for="dayPicker">Day</label>
      <input id="dayPicker" type="date">
      <button id="showLatest" type="button">Latest Day</button>
      <div id="summary" class="summary"></div>
    </div>
    <div id="content"></div>
  </div>
  <script>
    const entries = {data_json};
    const dayPicker = document.getElementById('dayPicker');
    const showLatest = document.getElementById('showLatest');
    const content = document.getElementById('content');
    const summary = document.getElementById('summary');
    const days = [...new Set(entries.map((entry) => entry.day))].sort().reverse();

    function render(day) {{
      const filtered = entries.filter((entry) => entry.day === day);
      summary.textContent = filtered.length ? `${{filtered.length}} clip(s) on ${{day}}` : `0 clip(s) on ${{day}}`;
      if (!filtered.length) {{
        content.innerHTML = '<div class="empty">No clips for the selected day.</div>';
        return;
      }}
      content.innerHTML = `<div class="cards">${{filtered.map((entry) => `
        <article class="card">
          ${{entry.screenshot_href ? `<img class="shot" src="${{entry.screenshot_href}}" alt="Representative screenshot for ${{entry.clip_file}}">` : '<div class="shot"></div>'}}
          <div class="body">
            <div class="eyebrow">${{entry.day}}</div>
            <h2 class="title">${{entry.time}}</h2>
            <div class="links">
              <a href="${{entry.clip_href}}" target="_blank" rel="noreferrer">Open clip</a>
              ${{entry.screenshot_href ? `<a href="${{entry.screenshot_href}}" target="_blank" rel="noreferrer">Open screenshot</a>` : ''}}
            </div>
            <pre>${{JSON.stringify(entry.json, null, 2)}}</pre>
          </div>
        </article>
      `).join('')}}</div>`;
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
    OUTPUT_PATH.write_text(build_html(load_entries()))
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()

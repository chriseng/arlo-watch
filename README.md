# arlo-watch

Downloads clips from a specific Arlo camera and generates AI-powered JSON summaries using the Gemini API. AI prompt is geared towards outdoor wildlife, so it may need tweaking for different use cases.

## How it works

1. `download.py` — authenticates to Arlo, fetches the library for the target camera, downloads any clips not already on disk.
2. `analyze.py` — for each `.mp4` without a matching `.json`, uploads the clip to Gemini, performs clip analysis, optionally runs a second animal-verification pass, and writes a JSON summary file alongside it.
3. `generate_gallery.py` — reads the clip summaries and screenshots, highlights verification disagreements in the rendered notable-events list, and builds the static gallery in `html/`.
4. `run.sh` — runs the full download, cleanup, analysis, and gallery-generation sequence; invoke this from cron.

Clips accumulate in `html/clips/`. Each clip gets a companion JSON file, e.g.:

```
html/clips/
  20240115_143022_UTC.mp4
  20240115_143022_UTC.json
  ...
```

Example JSON output:

```json
{
  "duration_seconds": 42,
  "persons": 0,
  "vehicles": 0,
  "animals": 1,
  "activity": "A house finch is perched on the edge of a birdbath and drinks water.",
  "notable_events": ["bird lands on birdbath edge", "bird drinks from birdbath"],
  "motion_area": "center",
  "time_of_day": "day",
  "confidence": "high",
  "screenshot_timestamp_seconds": 12.4,
  "evidence_timestamps_seconds": [11.8, 12.4, 13.1],
  "screenshot_reason": "The bird is most visible in this frame.",
  "screenshot_file": "20240115_143022_UTC.jpg",
  "verification": {
    "presence_conflict": false,
    "frame_assessment": "The contact sheet shows a clearly visible bird in multiple frames.",
    "activity_sample_frames": ["A2", "E2"],
    "activity_sample_frame_timestamps_seconds": {"A2": 12.0, "E2": 12.4},
    "verification_sample_timestamps_seconds": {"A1": 11.8, "A2": 12.0, "A3": 12.2, "B1": 12.4, "B2": 12.6, "B3": 12.8, "C1": 13.0, "C2": 13.2, "C3": 13.4, "D1": 11.8, "D2": 12.0, "D3": 12.2, "E1": 12.4, "E2": 12.6, "E3": 12.8, "F1": 13.0, "F2": 13.2, "F3": 13.4},
    "visible_subjects": ["house finch"],
    "confidence": "medium"
  },
  "timestamp_est": "2024-01-15T14:30:22-05:00",
  "clip_file": "20240115_143022_UTC.mp4"
}
```

All activity is logged to `arlo_watch.log` and stdout.

## Analysis behavior

`analyze.py` is intentionally strict about **whether any subject is present at all** and somewhat looser about **species naming once an animal is clearly visible**.

- Presence detection is conservative. The model is told not to infer animals, people, or vehicles from motion triggers, shadows, ripples, foliage, or scene context alone.
- Species identification is more permissive after presence is established. If a bird is clearly visible, the model should try to name the most specific visually supported species and use qualifiers like `likely` only when the ID is genuinely uncertain.
- The goal is to prefer false negatives on subject presence, while still allowing useful species labels when a real bird or animal is on camera.

### Multi-phase animal verification

Animal summaries use a two-stage flow:

1. Gemini analyzes the uploaded video clip and produces the normal JSON summary, including the representative screenshot timestamp and 1-3 evidence timestamps.
2. If and only if the first pass reports `animals > 0`, `analyze.py` builds two labeled 3x3 contact sheets from the same sampled timestamps and sends both images to Gemini in a single verification request: a full-scene sheet and a zoomed-in sheet. Each tile is `960x540`, so each verification image is `2880x1620`.
3. The verification pass may point to specific grid cells such as `A2`, `B3`, `D2`, or `F1` in `verification.activity_sample_frames`, where `A1`-`C3` come from the wide sheet and `D1`-`F3` come from the zoomed sheet. The saved JSON also records those frame timestamps in `verification.activity_sample_frame_timestamps_seconds` and records the full sampling map in `verification.verification_sample_timestamps_seconds`.
4. If verification cannot confirm an animal in the still frames, the original summary is preserved and `verification.presence_conflict` is set to `true`.
5. If verification does confirm an animal, the original `activity`, `notable_events`, and `screenshot_reason` are still preserved. The verification result is stored separately in the `verification` object as a second opinion rather than rewriting the saved analysis text.

Verification sampling is evidence-centered:

- If the main analysis returns 3 evidence timestamps, verification samples `t-0.4`, `t`, and `t+0.4` around each one.
- If it returns 2 evidence timestamps, verification samples `t-0.6`, `t-0.2`, `t+0.2`, and `t+0.6` around each one, then fills the remaining 2 slots with broad clip coverage.
- If it returns 1 evidence timestamp, verification uses a dense 9-frame burst centered on that point.
- If it returns 0 evidence timestamps, verification falls back to 9 whole-clip coverage samples.
- When early negative offsets collapse into duplicate `0.0` frames after de-duplication, lower-priority forward offsets are used to refill those lost slots before falling back to broader coverage.

This is deliberately asymmetric:

- Strict: "Is there actually an animal here?"
- Looser: "If there is an animal here, what species is it most likely to be?"

### Conflict display

`generate_gallery.py` does not rewrite saved clip JSON when the main summary and verification disagree about the animal label. Instead, it surfaces the disagreement at render time:

- If `verification.presence_conflict` is `true`, the gallery appends a `Hallucination warning: ...` line to the rendered notable-events list using `verification.frame_assessment`.
- If verification reports one clear `visible_subjects` label and that label does not appear in the main `activity` text, the gallery appends an `Alternate analysis: ...` line to the rendered notable-events list using `verification.frame_assessment`.
- New clip JSON stores verified grid labels in `verification.activity_sample_frames`, the timestamps for those cited labels in `verification.activity_sample_frame_timestamps_seconds`, and the full wide-plus-zoom verification sampling map in `verification.verification_sample_timestamps_seconds`.
- Older JSON files that have no `verification` object still render normally.

### Gemini call implications

The verification pass increases Gemini usage, but only for clips where the first pass claims an animal.

- Every analyzed clip uses 1 video upload and 1 Gemini generation call for the main clip analysis.
- Clips with `animals = 0` stop there.
- Clips with `animals > 0` add 2 contact-sheet image uploads and 1 extra Gemini generation call for verification.

In practice, that means:

- Non-animal clips keep the original quota profile.
- Suspected animal clips use a second Gemini call.
- If your feed has frequent wildlife activity, daily Gemini usage will rise accordingly.

---

## Prerequisites

- Debian/Ubuntu with Python 3.10+
- An Arlo account with at least one camera
- A Google account to generate a Gemini API key

---

## Setup

### 1. Get a Gemini API key

1. Go to [aistudio.google.com](https://aistudio.google.com) and sign in with your Google account.
2. Click **Get API key** → **Create API key**.
3. Copy the key — you'll add it to `.env` below.

As of April 30, 2026, the free tier allows 500 requests per day using `gemini-3.1-flash-lite-preview`. No billing setup required to start.

### 2. Install Python dependencies

```bash
cd arlo-watch
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `ARLO_USERNAME` | Your Arlo account email |
| `ARLO_PASSWORD` | Your Arlo account password |
| `ARLO_CAMERA_NAME` | Exact name of the camera in the Arlo app |
| `GEMINI_API_KEY` | API key from AI Studio |

Leave `ARLO_TFA_SOURCE=console` for the first run (see below).

### 4. First run — establish Arlo session

Arlo requires two-factor authentication. Run the download script interactively once to handle it:

```bash
source .venv/bin/activate
python3 download.py
```

To restrict a run to the newest clips in the current `DAYS_BACK` window:

```bash
python3 download.py --latest 5
```

To skip clips whose metadata reports certain `objCategory` values, add this to `.env`:

```env
EXCLUDED_OBJ_CATEGORIES=["Vehicle","Animal","Motion"]
```

When prompted, enter the 2FA code sent to your email. pyaarlo saves the session token to `.arlo_session/` so subsequent runs won't need it. Session tokens typically last several weeks.

If the session expires and you're running unattended via cron, see **Unattended 2FA** below.

### 5. Test the full pipeline

```bash
bash run.sh
```

Check `html/clips/` for downloaded `.mp4`, `.jpg`, and `.json` files, and `arlo_watch.log` for output. For animal clips, inspect the `verification` object in the generated JSON. If the gallery shows an `Alternate analysis:` event, that note is coming from `verification.frame_assessment` rather than from a rewritten clip summary.

### 6. Schedule with cron

Open your crontab:

```bash
crontab -e
```

Add a line to run every 30 minutes (adjust as needed):

```cron
*/30 * * * * cd /home/youruser/arlo-watch && source .venv/bin/activate && bash run.sh 2>&1 >> arlo_watch.log
```

---

## Unattended 2FA (optional)

If your Arlo session expires while running via cron, you need an automated way to supply the 2FA code. Edit `.env`:

```env
ARLO_TFA_SOURCE=imap
ARLO_TFA_TYPE=email
ARLO_TFA_HOST=imap.gmail.com
ARLO_TFA_USER=your@gmail.com
ARLO_TFA_PASSWORD=your_gmail_app_password
```

`ARLO_TFA_USER` should be the mailbox Arlo sends the code to. If omitted, the script falls back to `ARLO_USERNAME`, which only works when your Arlo login email and IMAP mailbox are the same.

For Gmail, generate an **App Password** (not your regular password) at myaccount.google.com → Security → App Passwords. This requires 2-Step Verification to be enabled on your Google account.

---

## Configuration reference

All settings go in `.env`.

Required:

| Variable | Description |
|---|---|
| `ARLO_USERNAME` | Arlo account email/username |
| `ARLO_PASSWORD` | Arlo account password |
| `ARLO_CAMERA_NAME` | Exact camera name to download from |
| `GEMINI_API_KEY` | Gemini API key used by `analyze.py` |

Common 2FA settings:

| Variable | Default | Description |
|---|---|---|
| `ARLO_TFA_SOURCE` | `console` | 2FA provider mode used by pyaarlo, such as interactive `console` or unattended `imap` |
| `ARLO_TFA_TYPE` | `email` | 2FA delivery type expected by pyaarlo |
| `ARLO_TFA_HOST` | unset | IMAP or REST host used for unattended 2FA |
| `ARLO_TFA_USER` | `ARLO_USERNAME` | Mailbox/API username used for unattended 2FA |
| `ARLO_TFA_PASSWORD` | `ARLO_PASSWORD` | Mailbox/API password or token used for unattended 2FA |
| `ARLO_TFA_NICKNAME` | unset | Optional mailbox/device nickname passed through to pyaarlo |
| `ARLO_TFA_TIMEOUT` | `3` | Seconds between IMAP/REST polling attempts |
| `ARLO_TFA_TOTAL_TIMEOUT` | `60` | Total seconds to wait for a 2FA code |
| `ARLO_TFA_RETRIES` | unset | Optional explicit retry count for the 2FA provider |
| `ARLO_TFA_DELAY` | unset | Optional delay between 2FA retries |
| `ARLO_TFA_CIPHER_LIST` | unset | Optional TLS cipher list override for the 2FA connection |

Other optional overrides:

| Variable | Default | Description |
|---|---|---|
| `CLIPS_DIR` | `html/clips` | Directory where clips, screenshots, and JSON are saved |
| `SESSION_DIR` | `.arlo_session` | pyaarlo session token storage |
| `DAYS_BACK` | `1` | How many days of library to fetch per run |
| `MIN_CLIP_DURATION_SECONDS` | `5` | Skip clips shorter than this threshold using metadata first, then MP4 duration verification after download |
| `EXCLUDED_OBJ_CATEGORIES` | unset | JSON array or comma-separated list of `objCategory` values to skip during download |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite-preview` | Gemini model to use for analysis |
| `STRIP_AUDIO_BEFORE_UPLOAD` | `false` | When `true`, `analyze.py` uses `ffmpeg` to create a temporary no-audio MP4 for upload to avoid models that reject audio input |
| `CLIP_RETENTION_DAYS` | `7` | Retention window used by `scripts/cleanup_old_clips.py` |

`download.py` accepts `--latest N` to restrict a run to the most recent `N` videos found within the configured `DAYS_BACK` window.

If you need to avoid model errors such as `Audio input modality is not enabled` (seen with `gemma-4-31b-it` for example), set this in `.env`:

```env
STRIP_AUDIO_BEFORE_UPLOAD=true
```

This is off by default. When enabled, `analyze.py` requires `ffmpeg` on your `PATH` and uploads a temporary video-only copy instead of the original MP4.

---

## Storage

`run.sh` automatically runs `scripts/cleanup_old_clips.py` on every cycle before analysis and gallery generation. By default, that script removes `.mp4` clips older than `CLIP_RETENTION_DAYS` (default `7`).

Plan for roughly 50–200 MB per day depending on clip frequency and length. If you want a different retention window, set `CLIP_RETENTION_DAYS` in `.env`:

```env
CLIP_RETENTION_DAYS=30
```

If you want cleanup to also remove `.json` summaries or `.jpg` screenshots for expired clips, run `scripts/cleanup_old_clips.py` manually with `--purge-summaries` and/or `--purge-screenshots`.

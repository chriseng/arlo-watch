# arlo-watch

Downloads clips from a specific Arlo camera and generates AI-powered JSON summaries using the Gemini API. AI prompt is geared towards outdoor wildlife, so it may need tweaking for different use cases.

## How it works

1. `download.py` — authenticates to Arlo, fetches the library for the target camera, downloads any clips not already on disk.
2. `analyze.py` — for each `.mp4` without a matching `.json`, uploads the clip to Gemini, performs clip analysis, optionally runs a second animal-verification pass, and writes a JSON summary file alongside it.
3. `generate_gallery.py` — reads the clip summaries and screenshots and builds the static gallery in `html/`.
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
    "overrode_subject_claims": false,
    "frame_assessment": "The contact sheet shows a clearly visible bird in multiple frames.",
    "animal_frames": ["A2", "B2"],
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
2. If and only if the first pass reports `animals > 0`, `analyze.py` builds a labeled 3x3 contact sheet from sampled frames across the clip and sends that sheet to Gemini for verification.
3. The verification pass must point to specific grid cells such as `A2` or `B3` in `verification.animal_frames` where an animal is actually visible.
4. If verification cannot confirm an animal in the still frames, the script overrides the original animal claim and rewrites the summary to an ambiguous/background-motion result.

This is deliberately asymmetric:

- Strict: "Is there actually an animal here?"
- Looser: "If there is an animal here, what species is it most likely to be?"

### Gemini call implications

The verification pass increases Gemini usage, but only for clips where the first pass claims an animal.

- Every analyzed clip uses 1 video upload and 1 Gemini generation call for the main clip analysis.
- Clips with `animals = 0` stop there.
- Clips with `animals > 0` add 1 contact-sheet image upload and 1 extra Gemini generation call for verification.

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

Check `html/clips/` for downloaded `.mp4`, `.jpg`, and `.json` files, and `arlo_watch.log` for output. For animal clips, inspect the `verification` object in the generated JSON if you need to understand why a wildlife claim was kept or overridden.

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

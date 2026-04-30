# arlo-watch

Downloads clips from a specific Arlo camera and generates AI-powered JSON summaries using the Gemini API.

## How it works

1. `download.py` — authenticates to Arlo, fetches the library for the target camera, downloads any clips not already on disk.
2. `analyze.py` — for each `.mp4` without a matching `.json`, uploads the clip to Gemini, waits for analysis, and writes a JSON summary file alongside it.
3. `run.sh` — runs both scripts in sequence; invoke this from cron.

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
  "persons": 1,
  "vehicles": 0,
  "animals": 0,
  "activity": "A person walks up the driveway and stops near the garage.",
  "notable_events": ["person entered frame from left", "person stopped near garage door"],
  "motion_area": "center",
  "time_of_day": "day",
  "confidence": "high",
  "clip_file": "20240115_143022_UTC.mp4"
}
```

All activity is logged to `arlo_watch.log` and stdout.

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

The free tier allows 1,500 requests per day, which covers up to 1,500 clips/day at no cost. No billing setup required to start.

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

When prompted, enter the 2FA code sent to your email. pyaarlo saves the session token to `.arlo_session/` so subsequent runs won't need it. Session tokens typically last several weeks.

If the session expires and you're running unattended via cron, see **Unattended 2FA** below.

### 5. Test the full pipeline

```bash
bash run.sh
```

Check `html/clips/` for downloaded `.mp4`, `.jpg`, and `.json` files, and `arlo_watch.log` for output.

### 6. Schedule with cron

Open your crontab:

```bash
crontab -e
```

Add a line to run every 30 minutes (adjust as needed):

```cron
*/30 * * * * /home/youruser/arlo-watch/.venv/bin/python3 /home/youruser/arlo-watch/download.py >> /home/youruser/arlo-watch/arlo_watch.log 2>&1 && /home/youruser/arlo-watch/.venv/bin/python3 /home/youruser/arlo-watch/analyze.py >> /home/youruser/arlo-watch/arlo_watch.log 2>&1
```

Or, using `run.sh`:

```cron
*/30 * * * * cd /home/youruser/arlo-watch && source .venv/bin/activate && bash run.sh >> arlo_watch.log 2>&1
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

All settings go in `.env`. Optional overrides:

| Variable | Default | Description |
|---|---|---|
| `CLIPS_DIR` | `html/clips` | Directory where clips, screenshots, and JSON are saved |
| `SESSION_DIR` | `.arlo_session` | pyaarlo session token storage |
| `DAYS_BACK` | `1` | How many days of library to fetch per run |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite-preview` | Gemini model to use for analysis |
| `ARLO_TFA_HOST` | unset | IMAP or REST host used for unattended 2FA |
| `ARLO_TFA_USER` | `ARLO_USERNAME` | Mailbox/API username used for unattended 2FA |
| `ARLO_TFA_PASSWORD` | `ARLO_PASSWORD` | Mailbox/API password or token used for unattended 2FA |
| `ARLO_TFA_TIMEOUT` | `3` | Seconds between IMAP/REST polling attempts |
| `ARLO_TFA_TOTAL_TIMEOUT` | `60` | Total seconds to wait for a 2FA code |

`download.py` accepts `--latest N` to restrict a run to the most recent `N` videos found within the configured `DAYS_BACK` window.

---

## Storage

Clips are not deleted automatically. Plan for roughly 50–200 MB per day depending on clip frequency and length. Add a cron job to purge old files if needed:

```bash
# Delete clips older than 30 days
find /home/youruser/arlo-watch/clips -name "*.mp4" -mtime +30 -delete
find /home/youruser/arlo-watch/clips -name "*.json" -mtime +30 -delete
```

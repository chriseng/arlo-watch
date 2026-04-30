import os
from dotenv import load_dotenv
import pyaarlo
from datetime import datetime, timedelta, timezone

load_dotenv()

ar = pyaarlo.PyArlo(
    username=os.environ["ARLO_USERNAME"],
    password=os.environ["ARLO_PASSWORD"],
    library_days=1,
    synchronous_mode=True,
    tfa_source=os.getenv("ARLO_TFA_SOURCE", "console"),
    tfa_type=os.getenv("ARLO_TFA_TYPE", "email"),
    storage_dir=os.getenv("SESSION_DIR", ".arlo_session"),
)

camera = next((c for c in ar.cameras if c.name == os.environ["ARLO_CAMERA_NAME"]), None)
if camera is None:
    print("Camera not found")
    exit(1)

camera.update_media(wait=True)
_, recordings = ar.ml.videos_for(camera)
cutoff = datetime.now(timezone.utc) - timedelta(days=1)
recordings = [
    r for r in recordings
    if r.created_at and datetime.fromtimestamp(r.created_at / 1000, tz=timezone.utc) >= cutoff
]

print(f"Found {len(recordings)} recording(s)\n")
for rec in recordings:
    print(f"  created_at : {rec.created_at}")
    print(f"  duration   : {getattr(rec, 'duration', 'ATTR_MISSING')}")
    # dump raw data so we can see every available field
    raw = getattr(rec, '_attrs', None) or getattr(rec, '_data', None) or vars(rec)
    print(f"  raw keys   : {list(raw.keys()) if isinstance(raw, dict) else raw}")
    print(vars(rec))
    print()

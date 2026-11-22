"""All settings come from environment variables (GitHub Actions secrets/vars)."""
import os
from datetime import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("AUTOPOST_TZ", "Asia/Kolkata"))

# Daily reel slots (local time). Max reels per account per day = len(REEL_SLOTS).
REEL_SLOTS = [time(7, 30), time(18, 0)]
# Slots you already filled by hand (all platforms), format "YYYY-MM-DD..YYYY-MM-DD@HH:MM;..."
# Default: 6 PM is already scheduled manually on every platform up to 29 Nov 2026.
RESERVED_SLOTS = os.getenv("RESERVED_SLOTS", "2026-11-22..2026-11-29@18:00")

# Main video slot(s): one main video per day.
MAIN_SLOTS = [time(20, 0)]

# Platforms that get reels / main videos right now.
REEL_PLATFORMS = [p.strip() for p in os.getenv("REEL_PLATFORMS", "instagram,facebook,dailymotion").split(",") if p.strip()]
MAIN_PLATFORMS = [p.strip() for p in os.getenv("MAIN_PLATFORMS", "dailymotion").split(",") if p.strip()]

# How far ahead Facebook can natively schedule a reel (Meta allows 10 min .. 29 days).
FB_MIN_LEAD_MIN = 15
FB_MAX_LEAD_DAYS = 28

# A queued slot is "missed" if we are this late; it is then moved to the next free slot.
MISSED_AFTER_MIN = int(os.getenv("MISSED_AFTER_MIN", "75"))

# Google Drive (read only, service account shared on the folder)
DRIVE_ROOT_ID = os.getenv("DRIVE_ROOT_ID", "")
GDRIVE_SA_JSON = os.getenv("GDRIVE_SA_JSON", "")

# Meta
META_API = os.getenv("META_API_VERSION", "v21.0")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "")          # long-lived Page access token
IG_USER_ID = os.getenv("IG_USER_ID", "")                # Instagram professional account id
IG_TOKEN = os.getenv("IG_TOKEN", "") or FB_PAGE_TOKEN   # token with instagram_content_publish

# Dailymotion (legacy REST API, password grant)
DM_API_KEY = os.getenv("DM_API_KEY", "")
DM_API_SECRET = os.getenv("DM_API_SECRET", "")
DM_USERNAME = os.getenv("DM_USERNAME", "")
DM_PASSWORD = os.getenv("DM_PASSWORD", "")
DM_BASE = os.getenv("DM_BASE", "https://api.dailymotion.com")

# GitHub (for the daily "pin these comments" checklist issue)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")

STATE_PATH = os.getenv("STATE_PATH", "state/state.json")
AI_LABEL = os.getenv("AI_LABEL", "1") == "1"   # your videos are AI-assisted -> disclose
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
WORKDIR = os.getenv("AUTOPOST_WORKDIR", "/tmp/autopost")

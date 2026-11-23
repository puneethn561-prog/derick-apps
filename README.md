# Social Autopost — Drive ➜ Instagram · Facebook · Dailymotion

Drop an **Upload Package** folder (same format as *Chivari Bussu EP2 - Upload Package*) into the Drive auto-upload folder.
GitHub Actions does the rest.

## Rules
| What | Where | When (IST) |
|---|---|---|
| Reels (9:16 only) | Instagram, Facebook, Dailymotion, YouTube (Shorts) | 07:00 and 18:00 — max 2 per account per day |
| Reel 16x9 version (`_16x9_` in name) | YouTube only, as a normal video (replaces the Short) | same slot as the reel |
| Main video (16:9, or longer than 2 min) | YouTube + Dailymotion only | one that day → 18:00; two → 14:00 (first) and 18:00 (second) |
| Till 29 Nov 2026 | 18:00 is already filled by hand → reels use only 07:00, main videos use 14:00 (`RESERVED_SLOTS`) |

* Facebook reels are uploaded ahead and **natively scheduled** by Facebook (10 min – 29 days).
* Instagram & Dailymotion have no scheduling in their API → the 07:15 / 17:45 / 19:45 runs wait and post on the minute.
* Wrong time / two videos in one slot / missed slot / failed upload → moved to the next free slot automatically (hourly).
* After a video goes live its **pinned comment** text is posted (Instagram, Facebook). Pinning is not in any API, so a
  daily GitHub issue "Pin comments — <date>" lists what to pin. Dailymotion's API can't post comments.
* `[YOUTUBE_LINK_EPx ...]` lines are removed until the link exists.
* First run = **fresh start**: everything already in Drive is marked skip. Only new packages get posted.
* State lives in `state/state.json` (committed by the bot) — that's the source of truth.

## Package format (what the bot reads)
```
00_Text_Instagram_*.md   00_Text_Facebook_*.md   00_Text_Dailymotion_*.md
   sections:  ## Reel1 — Instagram   (**Title:**, **Caption:**/**Description:** ```...```, **Pinned / first comment:** ```...```)
01_Teaser_11Nov_9x16_1080.mp4     -> reel
02_Main_12Nov_1080p.mp4 / _4K.mp4 -> main (1080p preferred)
03_Reel1_13Nov_9x16_1080.mp4      -> reel
03_Reel1_13Nov_16x9_1080.mp4      -> same reel, 16:9 copy for YouTube
03_Reel1_13Nov_Cover_fb_9x16.jpg  -> Facebook cover
```
Order = the number prefix.

**Fixed date & time (optional):** inside any section add a line like
`- **Post date/time:** 25-11-2026 (Wednesday) at **6:00 PM IST**` (kit format, also `**Schedule:** 2026-11-25 18:00`, `25-11-2026 6:00 PM`, `25 Nov 2026 9:30 am`).
That post goes out at exactly that time (IST) on that platform. Whatever was planned in the same slot
(morning = before 1 PM, evening = after) is pushed to the next free slot, so the 2 reels/day cap holds.
Facebook schedules natively to the minute; Instagram/Dailymotion/YouTube are posted by the hourly run. Packages are processed in folder-name order.

## One-time setup (repo → Settings → Secrets and variables → Actions)
**Variables:** `DRIVE_ROOT_ID`, `FB_PAGE_ID`, `IG_USER_ID`  (optional `RESERVED_SLOTS`, `DRY_RUN=1` for testing)
**Secrets:**
| Secret | How to get it |
|---|---|
| `GDRIVE_SA_JSON` | Google Cloud → new project → enable **Drive API** → Service account → Keys → JSON. Share the Drive folder with the service-account email (Viewer). |
| `FB_PAGE_TOKEN` | Meta app (Business type) → Graph API Explorer → permissions `pages_show_list, pages_read_engagement, pages_manage_posts, pages_manage_engagement, instagram_basic, instagram_content_publish, instagram_manage_comments` → get **long-lived Page token**. |
| `IG_TOKEN` | Same token works when Instagram is a Business/Creator account linked to the Page (leave empty to reuse). |
| `DM_API_KEY`, `DM_API_SECRET` | Dailymotion Studio → Settings → API keys → **Private API key** (video permissions). Optional var `DM_PROFILE_ID`. |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | Google Cloud (same project) → enable **YouTube Data API v3** → OAuth client (Web) → OAuth Playground with own credentials, scopes `youtube.upload youtube.force-ssl` → refresh token. App must be *In production* (testing tokens expire in 7 days). Uploads stay Private until the YouTube API audit passes. |

Then: Actions → autopost → **Run workflow** with `mode=plan, dry_run=1` and check the log.

## Run locally
```
pip install -r requirements.txt pytest
python -m pytest tests
DRY_RUN=1 python -m autopost plan
```

## Long videos
Any video longer than 2 minutes (Drive duration, `LONG_VIDEO_SECONDS`, default 120) is treated as a long/main video and goes only to YouTube + Dailymotion (main slots 14:00 / 18:00 IST), never to Instagram/Facebook.

## Auto delete
After every post from a Drive zip/folder is published, `state.json` lists it under `cleanup` with its last post time. A daily Hark job moves it to Drive Trash 30 days later (`DELETE_AFTER_DAYS`).

## Clash rule and daily check
- Two videos at the same date + time on the same platform → the later one is pushed to its next free slot.
- The planner runs every hour: any post at a wrong time, a clash, a missed slot or a failed upload is moved/retried automatically, and already-scheduled posts (Facebook, YouTube) are rescheduled on the platform.

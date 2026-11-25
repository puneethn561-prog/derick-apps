# Social Autopost — Drive ➜ Instagram · Facebook · Dailymotion

Drop an **Upload Package** folder (same format as *Chivari Bussu EP2 - Upload Package*) into the Drive auto-upload folder.
GitHub Actions does the rest.

## Upload kit: any file type works

Put the video, the cover pictures and ONE kit file in a folder or a zip inside the Upload Kit folder.
The kit file can be **.txt, .md, .pdf, .docx, .odt, .rtf, .html, .json or a Google Doc**.
Write a heading per platform (YouTube / Instagram / Facebook / Dailymotion) and under it `Title:`, `Description:` or `Caption:`,
`Tags:`, `Hashtags:`. An `Upload time:` / `Schedule:` line sets the time; a time already past goes to the next free slot.
A picture with `16x9` in its name is used as the Dailymotion thumbnail; the vertical one for the others.

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
**Variables:** `DRIVE_ROOT_ID`, `FB_PAGE_ID`, `IG_USER_ID`  (optional `RESERVED_SLOTS`, `DRY_RUN=1` for testing, `YT_ENABLED=0` keeps YouTube off until the API audit is approved; videos planned while off are not back-filled)
**Secrets:**
| Secret | How to get it |
|---|---|
| `GDRIVE_SA_JSON` | Google Cloud → new project → enable **Drive API** → Service account → Keys → JSON. Share the Drive folder with the service-account email (Viewer). |
| `FB_PAGE_TOKEN` | Meta app (Business type) → Graph API Explorer → permissions `pages_show_list, pages_read_engagement, pages_manage_posts, pages_manage_engagement, instagram_basic, instagram_content_publish, instagram_manage_comments` → get **long-lived Page token**. |
| `IG_TOKEN` | Same token works when Instagram is a Business/Creator account linked to the Page (leave empty to reuse). |
| `DM_API_KEY`, `DM_API_SECRET` | Dailymotion Studio → Settings → API keys → **Private API key** (video permissions). Optional var `DM_PROFILE_ID`. |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | Google Cloud (same project) → enable **YouTube Data API v3** → OAuth client (Web) → OAuth Playground with own credentials, scopes `youtube.upload youtube.force-ssl` → refresh token. App must be *In production* (testing tokens expire in 7 days). Uploads stay Private until the YouTube API audit passes. |

Optional variable `YT_DAILY_QUOTA` (default 10000) if Google raised your YouTube quota.

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

## Failure alerts
Anything that fails in a `plan` or `slot` run (upload, comment, cover/thumbnail, a login, a post held by the pre-upload check, a crash) is added as one comment to the GitHub issue **"Autopost alert — <date>"** (label `autopost-alert`; GitHub emails the repo owner). Each row says the time (IST), platform, item, step, the error, and a plain fix hint for known errors: YouTube quota used up, YouTube `invalid_grant` (make a new `YT_REFRESH_TOKEN`), Meta token expired (code 190 → new `FB_PAGE_TOKEN` / `IG_TOKEN`), Dailymotion login failed, 401/403. A clean run with nothing held closes the open alert issues automatically. A login check of every platform runs once a day (first `plan` run after 06:00 IST) so an expired token shows up before a post fails. With `DRY_RUN=1` no issue is opened; the alert is only printed.

## YouTube quota guard
The YouTube Data API allows `YT_DAILY_QUOTA` units a day (repo variable, default 10000), reset at midnight Pacific (13:30 IST in winter, 12:30 IST in summer). The bot counts its own use in `state.json` (`yt_quota`): upload 1600, thumbnail 50, comment 50, list 1. An upload that would go over the limit, or one YouTube answers with `quotaExceeded`, is not lost: it moves to the next free slot of its track after the reset (`deferred: quota`, `not_before`), the planner won't pull it back earlier, and an alert row says where it went. A pinned comment that doesn't fit waits for the next run.

## Schedule audit (`mode=audit`)
Report only — changes nothing. Checks every upcoming (queued / scheduled / held) post against the slot rules: reels only 07:00 / 18:00, main videos only 14:00 / 18:00 (fixed times from a package allowed), no two videos at the same time on one platform, max one post per track slot, a lone main video at 18:00, `RESERVED_SLOTS` left free, nothing missed, and Facebook's own scheduled time matching the state. The result is printed and added as a comment to the issue **"autopost-schedule-audit"** (label `autopost-audit`). Run it from Actions → autopost → Run workflow → `mode=audit`.

## Weekly report (`mode=weekly`)
Every Sunday 20:00 IST (cron `30 14 * * 0`) the bot sums up the last 7 days: what went out per platform, failure alerts, posts still failed or held, and views where one cheap call gets them (YouTube `videos.list` 1 unit, Dailymotion `views_total`, Instagram `views`, Facebook Reel plays). A view count that can't be read shows "n/a", never a guess. It is posted as a GitHub issue (label `autopost-weekly`) and closed at once, so it only lands in your email.

## Pre-upload checks
Before slots are assigned, every new post is checked against the platform limits using the size, length and width × height Drive already knows (no download): Instagram Reels 3 s–15 min, ≤ 300 MB, caption ≤ 2200, ≤ 30 hashtags; Facebook Reels 3–90 s and vertical 9:16; YouTube Shorts ≤ 3 min, no `<` `>`, tags ≤ 500 chars; Dailymotion ≤ 4 GB; empty files. A **hard error holds** that post (status `held`, no slot, listed in the alert issue); fix the package in Drive and the next hourly run releases it and gives it a slot. **Warnings** (too-long titles that get cut, many YouTube hashtags, unknown length, wrong aspect on non-Facebook platforms) go to the daily checklist issue and the post goes ahead. Each problem is reported once.

## Schedule preview (`mode=preview`)
Dry: posts nothing and doesn't change `state.json`. Runs the real checks and planner on a copy of the state with the current Drive packages and writes `state/preview.json`: every post planned in the next 7 days (date, time IST, platform, title, reel/main, status, note such as "fixed time from package" or "moved: YouTube quota"), held posts with their reasons, and the hand-filled `RESERVED_SLOTS` in that window. The same rows are printed in the run log. Run it with `mode=preview, dry_run=1`.

## Thumbnails
`Cover_ig_` / `Cover_fb_` / `Cover_yt_` / `Cover_dm_` images map to each platform. The bot sets the Dailymotion thumbnail itself after upload (`Cover_dm_`, or the first `Thumb_` image for a main video; portrait images go to the vertical thumbnail). No image → Dailymotion picks a frame.

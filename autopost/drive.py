"""Read-only Google Drive access + parser for the existing "Upload Package" format.

A package folder looks like (Chivari Bussu EP2 - Upload Package):
  00_Text_Instagram_titles_desc_comments.md   <- '## Reel1 — Instagram' sections
  00_Text_Facebook_titles_desc_comments.md
  00_Text_Dailymotion_titles_desc_comments.md
  01_Teaser_11Nov_9x16_1080.mp4
  02_Main_12Nov_4K.mp4                        <- main video (16:9)
  03_Reel1_13Nov_9x16_1080.mp4
  03_Reel1_13Nov_16x9_1080.mp4                <- optional 16:9 reel -> YouTube (normal video, not Short)
  03_Reel1_13Nov_Cover_ig_9x16.jpg / _fb_ / _yt_ / _dm_   (_dm_ -> Dailymotion thumbnail)
  02_Main_12Nov_Thumb_A.jpg ...

DRIVE_ROOT_ID = a folder that holds package folders (every subfolder with 00_Text_*.md is a package),
or one package folder itself. A .zip file in the root (or in a package folder) is opened in place:
every folder inside the zip that holds 00_Text_*.md files, or any kit file (pdf, txt, docx, md, rtf, html, json), is a package. Only the zip's index and text
files are read on each run (HTTP range requests); a video is extracted only when it is posted, and its
length is measured once and cached in state/durations.json.
"""
import io
import json
import os
import re
import shutil
import subprocess
import zipfile

from . import config

FOLDER = "application/vnd.google-apps.folder"
PLATFORM_FILES = {"instagram": "instagram", "facebook": "facebook", "dailymotion": "dailymotion", "youtube": "youtube"}
FILE_RE = re.compile(r"^(\d+)_([A-Za-z]+\d*)_([^_]+)_(.+)$")


# ---------------------------------------------------------------- markdown parsing
def parse_platform_md(text, platform):
    """Return {key: {'title','caption','tags','pinned'}} from one 00_Text_<Platform>_*.md file."""
    out = {}
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    for sec in sections:
        head, _, body = sec.partition("\n")
        m = re.match(r"\s*([A-Za-z]+\d*)\s*[—–-]\s*(\w+)", head)
        if not m or m.group(2).lower() != platform:
            continue
        key = m.group(1)
        title = re.search(r"\*\*Title:\*\*\s*(.+)", body)
        tags = re.search(r"\*\*Tags[^*]*:\*\*\s*(.+)", body)
        cap = re.search(r"\*\*(?:Caption|Description):\*\*\s*```[a-z]*\n(.*?)```", body, re.S)
        pin = re.search(r"\*\*Pinned[^*]*:\*\*\s*```[a-z]*\n(.*?)```", body, re.S)
        caption = cap.group(1).strip() if cap else ""
        when = re.search(r"\*\*(?:Schedule|Post\s*(?:date|time|at)|Date)[^*]*:\*\*\s*(.+)", body, re.I)
        out[key] = {
            "schedule": parse_when(when.group(1)) if when else None,
            "title": title.group(1).strip() if title else (caption.splitlines()[0] if caption else key),
            "caption": caption,
            "tags": [t.strip() for t in tags.group(1).split(",")] if tags else [],
            "pinned": pin.group(1).strip() if pin else "",
        }
    return out


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_when(raw):
    """'2026-11-25 18:00' | '25-11-2026 6:00 PM' | '25/11/2026 18:00' | '25 Nov 2026 6 PM' -> ISO (IST) or None."""
    from datetime import datetime
    s = raw.strip().strip("`*").lower().replace(".", ":")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
        if m:
            d, mo, y = map(int, m.groups())
        else:
            m = re.search(r"(\d{1,2})\s*([a-z]{3})[a-z]*\s*(\d{4})", s)
            if not m or m.group(2) not in _MONTHS:
                return None
            d, mo, y = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
    rest = s[m.end():]
    t = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", rest)
    if not t:
        return None
    h, mi = int(t.group(1)), int(t.group(2) or 0)
    if t.group(3) == "pm" and h < 12:
        h += 12
    if t.group(3) == "am" and h == 12:
        h = 0
    try:
        return datetime(y, mo, d, h, mi, tzinfo=config.TZ).isoformat()
    except ValueError:
        return None


def fill_links(text, links):
    """Replace [YOUTUBE_LINK_EP2 ...] style placeholders; drop the line if we have no link yet."""
    def repl_line(line):
        m = re.search(r"\[([A-Z_]*LINK[A-Z0-9_]*)[^\]]*\]", line)
        if not m:
            return line
        val = links.get(m.group(1))
        return line[:m.start()] + val + line[m.end():] if val else None
    return "\n".join(l for l in (repl_line(x) for x in text.splitlines()) if l is not None)


def duration_s(f):
    """Video length in seconds from Drive's videoMediaMetadata (0 when Drive hasn't processed it)."""
    try:
        return int((f.get("videoMediaMetadata") or {}).get("durationMillis") or 0) / 1000
    except (TypeError, ValueError):
        return 0


def dims(f):
    m = f.get("videoMediaMetadata") or {}
    try:
        return int(m.get("width") or 0), int(m.get("height") or 0)
    except (TypeError, ValueError):
        return 0, 0


def is_wide(f):
    """16:9 / landscape video: by real size when known, else by file name."""
    w, h = dims(f)
    if w and h and w != h:
        return w > h
    return bool(re.search(r"16x9|16_9|horizontal|landscape", f["name"], re.I))


def is_vertical(f):
    w, h = dims(f)
    if w and h and w != h:
        return h > w
    return bool(re.search(r"9x16|9_16|vertical|portrait", f["name"], re.I))


def build_items(pkg_id, pkg_name, files, texts):
    """Group package files into items (Teaser, Main, Reel1..)."""
    groups = {}
    for f in files:
        m = FILE_RE.match(f["name"])
        if not m:
            continue
        order, key, _date, rest = m.groups()
        g = groups.setdefault(key, {"order": int(order), "videos": [], "covers": {}, "thumbs": []})
        low = rest.lower()
        if low.endswith((".mp4", ".mov", ".m4v")):
            g["videos"].append(f)
        elif low.startswith("cover_"):
            plat = low.split("_")[1]
            g["covers"][{"ig": "instagram", "fb": "facebook", "yt": "youtube", "dm": "dailymotion"}.get(plat, plat)] = f
        elif low.startswith("thumb_"):
            g["thumbs"].append(f)
    items = []
    for key, g in sorted(groups.items(), key=lambda kv: kv[1]["order"]):
        if not g["videos"]:
            continue
        vertical = [v for v in g["videos"] if is_vertical(v)]
        wide = [v for v in g["videos"] if v not in vertical and is_wide(v)]
        video_wide = None
        # Long video = longer than LONG_VIDEO_SECONDS (2 min): always goes as a main video to YouTube + Dailymotion only.
        long_vids = [v for v in g["videos"] if duration_s(v) > config.LONG_VIDEO_SECONDS]
        if long_vids:
            kind = "main"
            hd = [v for v in long_vids if "1080" in v["name"]]
            video = hd[0] if hd else sorted(long_vids, key=lambda v: int(v.get("size") or 0))[0]
        elif vertical:
            # reel: 9x16 goes to IG/FB/DM (and YT Shorts); a 16x9 version, if present, goes to YouTube as a normal video
            kind = "reel"
            video = vertical[0] if vertical else wide[0]
            video_wide = wide[0] if wide else None
        else:
            kind = "main"
            hd = [v for v in g["videos"] if "1080" in v["name"]]
            video = hd[0] if hd else sorted(g["videos"], key=lambda v: int(v.get("size") or 0))[0]
        text = {p: t[key] for p, t in texts.items() if key in t}
        items.append({"id": f"{pkg_id}:{key}", "name": f"{pkg_name} / {key}", "key": key, "kind": kind,
                      "order": g["order"], "video": video, "video_wide": video_wide, "covers": _same_cover(g["covers"]),
                      "thumbs": sorted(g["thumbs"], key=lambda t: t["name"]), "text": text})
    return items


def _same_cover(covers):
    """One cover given for any platform -> used on every platform that has none (one thumbnail everywhere)."""
    if not covers:
        return covers
    one = covers.get("instagram") or covers.get("facebook") or covers.get("youtube") or next(iter(covers.values()))
    return {p: covers.get(p) or one for p in ("instagram", "facebook", "youtube", "dailymotion")}


def build_kit_item(pkg_id, pkg_name, files, kit):
    """One video + thumbnail + an upload kit (pdf/txt/docx/md/Google Doc...) -> one item. Kind by length (> 2 min = long video)."""
    vids = [f for f in files if f["name"].lower().endswith((".mp4", ".mov", ".m4v"))]
    if not vids:
        return []
    wide = [v for v in vids if is_wide(v)]
    main_v = [v for v in vids if v not in wide] or vids
    video = main_v[0]
    imgs = [f for f in files if f["name"].lower().endswith((".jpg", ".jpeg", ".png"))]
    named = [i for i in imgs if "thumb" in i["name"].lower() or "cover" in i["name"].lower()] or imgs
    wide_i = [i for i in named if re.search(r"16x9|16_9|horizontal|landscape|wide", i["name"], re.I)]
    tall_i = [i for i in named if i not in wide_i]
    # 16:9-only package or > 2 min -> main video (YouTube + Dailymotion only)
    long_v = duration_s(video) > config.LONG_VIDEO_SECONDS or is_wide(video)
    # one thumbnail for every platform, matching the video's shape (owner's rule, 25 Nov 2026)
    thumb = ((wide_i if is_wide(video) else tall_i) or named or [None])[0]
    text = {p: v for p, v in kit.items() if not p.startswith("_")}
    key = re.sub(r"[^A-Za-z0-9]+", "", kit.get("_name") or pkg_name)[:40] or "Video"
    return [{"id": f"{pkg_id}:{key}", "name": f"{pkg_name} / {kit.get('_name') or key}", "key": key,
             "kind": "main" if long_v else "reel", "order": 1, "video": video,
             "video_wide": wide[0] if (wide and not long_v) else None,
             "covers": ({p: thumb for p in ("instagram", "facebook", "youtube", "dailymotion")} if thumb else {}),
             "thumbs": [thumb] if thumb else [], "text": text}]


# ---------------------------------------------------------------- zip helpers
def is_zip(f):
    return f["name"].lower().endswith(".zip") or f.get("mimeType") in ("application/zip", "application/x-zip-compressed")


def probe(path):
    """{'ms', 'w', 'h'} of a video (display size, rotation applied)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:stream_side_data=rotation:format=duration", "-of", "json", path],
                             capture_output=True, text=True, timeout=120).stdout
        j = json.loads(out or "{}")
        st = (j.get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        rot = next((abs(int(x.get("rotation", 0))) for x in st.get("side_data_list") or [] if "rotation" in x), 0)
        if rot in (90, 270):
            w, h = h, w
        return {"ms": int(float((j.get("format") or {}).get("duration") or 0) * 1000), "w": w, "h": h}
    except Exception:
        return {"ms": 0, "w": 0, "h": 0}


def probe_ms(path):
    return probe(path)["ms"]


def _meta(v):
    """Cached probe -> Drive-style videoMediaMetadata (old cache entries are plain ms ints)."""
    if isinstance(v, dict):
        return {"durationMillis": v.get("ms", 0), "width": v.get("w", 0), "height": v.get("h", 0)}
    return {"durationMillis": v or 0}


DURATIONS_PATH = os.path.join(os.path.dirname(config.STATE_PATH) or ".", "durations.json")


def load_durations():
    try:
        with open(DURATIONS_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_durations(d):
    os.makedirs(os.path.dirname(DURATIONS_PATH) or ".", exist_ok=True)
    with open(DURATIONS_PATH, "w") as fh:
        json.dump(d, fh, indent=1, sort_keys=True)


class DriveRangeFile(io.RawIOBase):
    """Seekable read-only view of a Drive file using HTTP Range requests, so zipfile can read
    the index and single entries without downloading the whole zip."""
    BLOCK = 8 * 1024 * 1024

    def __init__(self, svc, file_id, size):
        self.svc, self.file_id, self.size, self.pos = svc, file_id, size, 0
        self._cache = (None, b"")
        if not size:
            meta = svc.files().get(fileId=file_id, fields="size", supportsAllDrives=True).execute()
            self.size = int(meta["size"])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def _fetch(self, start, end):
        url = f"https://www.googleapis.com/drive/v3/files/{self.file_id}?alt=media&supportsAllDrives=true"
        resp, content = self.svc._http.request(url, headers={"Range": f"bytes={start}-{end}"})
        if int(resp.status) not in (200, 206):
            raise IOError(f"Drive range read {resp.status}")
        return content

    def read(self, n=-1):
        if self.pos >= self.size:
            return b""
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        start0, buf = self._cache
        if start0 is not None and start0 <= self.pos and self.pos + n <= start0 + len(buf):
            out = buf[self.pos - start0:self.pos - start0 + n]
        elif n >= self.BLOCK:
            out = self._fetch(self.pos, self.pos + n - 1)
        else:
            end = min(self.size, self.pos + max(n, self.BLOCK)) - 1
            buf = self._fetch(self.pos, end)
            self._cache = (self.pos, buf)
            out = buf[:n]
        self.pos += len(out)
        return out

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


# ---------------------------------------------------------------- Drive client
class Drive:
    def __init__(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_info(
            json.loads(config.GDRIVE_SA_JSON), scopes=["https://www.googleapis.com/auth/drive.readonly"])
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    def ls(self, parent_id):
        out, token = [], None
        while True:
            r = self.svc.files().list(q=f"'{parent_id}' in parents and trashed = false",
                                      fields="nextPageToken, files(id,name,mimeType,size,videoMediaMetadata(durationMillis,width,height))", pageToken=token,
                                      pageSize=500, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            out += r.get("files", [])
            token = r.get("nextPageToken")
            if not token:
                return sorted(out, key=lambda f: f["name"])

    def read_bytes(self, file_id):
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, self.svc.files().get_media(fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()

    def read_text(self, file_id):
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, self.svc.files().get_media(fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue().decode("utf-8-sig")

    def _package(self, folder_id, name, files):
        texts = {}
        for f in files:
            m = re.match(r"^00_Text_([A-Za-z]+)_", f["name"])
            if m and m.group(1).lower() in PLATFORM_FILES and f["name"].endswith(".md"):
                plat = m.group(1).lower()
                texts[plat] = parse_platform_md(self.read_text(f["id"]), plat)
        if texts:
            return build_items(folder_id, name, files, texts)
        from . import kittext
        for pf in sorted([f for f in files if kittext.is_candidate(f)], key=lambda f: not f["name"].lower().endswith(".pdf")):
            try:
                if pf.get("mimeType") == kittext.GDOC:
                    t = self.svc.files().export(fileId=pf["id"], mimeType="text/plain").execute().decode("utf-8-sig")
                else:
                    t = kittext.to_text(pf["name"], self.read_bytes(pf["id"]))
                kit = kittext.parse(t)
            except Exception as e:
                print(f"[kit] cannot read {pf['name']}: {e}")
                continue
            if kit:
                return build_kit_item(folder_id, name, files, kit)
        return []

    def items(self, root_id=None):
        root_id = root_id or config.DRIVE_ROOT_ID
        root = self.ls(root_id)
        items = []
        if any(f["name"].startswith("00_Text_") or f["name"].lower().endswith(".pdf") for f in root):
            items += self._package(root_id, "package", root)
        for f in root:
            if f["mimeType"] == FOLDER:
                sub = self.ls(f["id"])
                items += self._package(f["id"], f["name"], sub)
                items += self._zip_items([z for z in sub if is_zip(z)])
        items += self._zip_items([z for z in root if is_zip(z)])
        return items

    # ------------------------------------------------------------ zip packages
    def _zip_items(self, zips):
        items = []
        for z in zips:
            try:
                items += self._zip_package(z)
            except Exception as e:  # a broken zip must not stop the other packages
                print(f"[zip] cannot read {z['name']}: {e}")
        return items

    def _zip_package(self, z):
        zf = zipfile.ZipFile(DriveRangeFile(self.svc, z["id"], int(z.get("size") or 0)))
        dirs = {}
        for info in zf.infolist():
            if info.is_dir() or "__MACOSX" in info.filename or os.path.basename(info.filename).startswith("."):
                continue
            d, name = os.path.split(info.filename)
            dirs.setdefault(d, []).append({
                "id": f"{z['id']}::{info.filename}", "name": name, "mimeType": "", "size": info.file_size,
                "zip_id": z["id"], "zip_size": int(z.get("size") or 0), "zip_entry": info.filename,
                "videoMediaMetadata": _meta(self.durations.get(f"{z['id']}::{info.filename}"))})
        items = []
        base = re.sub(r"\.zip$", "", z["name"], flags=re.I)
        for d, files in sorted(dirs.items()):
            texts = {}
            for f in files:
                m = re.match(r"^00_Text_([A-Za-z]+)_", f["name"])
                if m and m.group(1).lower() in PLATFORM_FILES and f["name"].endswith(".md"):
                    texts[m.group(1).lower()] = parse_platform_md(
                        zf.read(f["zip_entry"]).decode("utf-8-sig"), m.group(1).lower())
            if not texts:
                from . import kittext
                cands = sorted([f for f in files if kittext.is_candidate(f)], key=lambda f: not f["name"].lower().endswith(".pdf"))
                for pf in cands:
                    try:
                        kit = kittext.parse(kittext.to_text(pf["name"], zf.read(pf["zip_entry"])))
                    except Exception as e:
                        print(f"[kit] cannot read {pf['name']}: {e}")
                        continue
                    if kit:
                        self._measure(zf, files)
                        items += build_kit_item(f"{z['id']}::{d}", f"{base}/{d}" if d else base, files, kit)
                        break
                continue
            self._measure(zf, files)
            name = f"{base}/{d}" if d else base
            items += build_items(f"{z['id']}::{d}", name, files, texts)
        return items

    def _measure(self, zf, files):
        """Video length for zip entries (Drive can't see inside a zip); measured once, cached."""
        changed = False
        for f in files:
            vm = f["videoMediaMetadata"]
            if not f["name"].lower().endswith((".mp4", ".mov", ".m4v")) or (vm.get("durationMillis") and vm.get("width")):
                continue
            path = self.download(f, zf=zf)
            pr = probe(path)
            if pr["ms"]:
                self.durations[f["id"]] = pr
                f["videoMediaMetadata"] = _meta(pr)
                changed = True
        if changed:
            save_durations(self.durations)

    @property
    def durations(self):
        if not hasattr(self, "_durations"):
            self._durations = load_durations()
        return self._durations

    def download(self, f, zf=None):
        if f.get("zip_id"):
            os.makedirs(config.WORKDIR, exist_ok=True)
            path = os.path.join(config.WORKDIR, f"{f['zip_id']}_{f['zip_entry'].replace('/', '_')}")
            if not os.path.exists(path):
                zf = zf or zipfile.ZipFile(DriveRangeFile(self.svc, f["zip_id"], f["zip_size"]))
                with zf.open(f["zip_entry"]) as src, open(path + ".part", "wb") as dst:
                    shutil.copyfileobj(src, dst, 16 * 1024 * 1024)
                os.rename(path + ".part", path)
            return path
        return self._download_drive(f)

    def _download_drive(self, f):
        from googleapiclient.http import MediaIoBaseDownload
        os.makedirs(config.WORKDIR, exist_ok=True)
        path = os.path.join(config.WORKDIR, f"{f['id']}_{f['name']}")
        if not os.path.exists(path):
            with open(path + ".part", "wb") as fh:
                dl = MediaIoBaseDownload(fh, self.svc.files().get_media(fileId=f["id"], supportsAllDrives=True),
                                         chunksize=64 * 1024 * 1024)
                done = False
                while not done:
                    _, done = dl.next_chunk()
            os.rename(path + ".part", path)
        return path

"""Read-only Google Drive access + parser for the existing "Upload Package" format.

A package folder looks like (Chivari Bussu EP2 - Upload Package):
  00_Text_Instagram_titles_desc_comments.md   <- '## Reel1 — Instagram' sections
  00_Text_Facebook_titles_desc_comments.md
  00_Text_Dailymotion_titles_desc_comments.md
  01_Teaser_11Nov_9x16_1080.mp4
  02_Main_12Nov_4K.mp4                        <- main video (16:9)
  03_Reel1_13Nov_9x16_1080.mp4
  03_Reel1_13Nov_Cover_ig_9x16.jpg / _fb_ / _yt_
  02_Main_12Nov_Thumb_A.jpg ...

DRIVE_ROOT_ID = a folder that holds package folders (every subfolder with 00_Text_*.md is a package),
or one package folder itself.
"""
import io
import json
import os
import re

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
        out[key] = {
            "title": title.group(1).strip() if title else (caption.splitlines()[0] if caption else key),
            "caption": caption,
            "tags": [t.strip() for t in tags.group(1).split(",")] if tags else [],
            "pinned": pin.group(1).strip() if pin else "",
        }
    return out


def fill_links(text, links):
    """Replace [YOUTUBE_LINK_EP2 ...] style placeholders; drop the line if we have no link yet."""
    def repl_line(line):
        m = re.search(r"\[([A-Z_]*LINK[A-Z0-9_]*)[^\]]*\]", line)
        if not m:
            return line
        val = links.get(m.group(1))
        return line[:m.start()] + val + line[m.end():] if val else None
    return "\n".join(l for l in (repl_line(x) for x in text.splitlines()) if l is not None)


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
            g["covers"][{"ig": "instagram", "fb": "facebook", "yt": "youtube"}.get(plat, plat)] = f
        elif low.startswith("thumb_"):
            g["thumbs"].append(f)
    items = []
    for key, g in sorted(groups.items(), key=lambda kv: kv[1]["order"]):
        if not g["videos"]:
            continue
        vertical = [v for v in g["videos"] if "9x16" in v["name"]]
        if vertical:
            kind, video = "reel", vertical[0]
        else:
            kind = "main"
            hd = [v for v in g["videos"] if "1080" in v["name"]]
            video = hd[0] if hd else sorted(g["videos"], key=lambda v: int(v.get("size") or 0))[0]
        text = {p: t[key] for p, t in texts.items() if key in t}
        items.append({"id": f"{pkg_id}:{key}", "name": f"{pkg_name} / {key}", "key": key, "kind": kind,
                      "order": g["order"], "video": video, "covers": g["covers"],
                      "thumbs": sorted(g["thumbs"], key=lambda t: t["name"]), "text": text})
    return items


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
                                      fields="nextPageToken, files(id,name,mimeType,size)", pageToken=token,
                                      pageSize=500, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            out += r.get("files", [])
            token = r.get("nextPageToken")
            if not token:
                return sorted(out, key=lambda f: f["name"])

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
        return build_items(folder_id, name, files, texts) if texts else []

    def items(self):
        root = self.ls(config.DRIVE_ROOT_ID)
        if any(f["name"].startswith("00_Text_") for f in root):
            return self._package(config.DRIVE_ROOT_ID, "package", root)
        items = []
        for f in root:
            if f["mimeType"] == FOLDER:
                items += self._package(f["id"], f["name"], self.ls(f["id"]))
        return items

    def download(self, f):
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

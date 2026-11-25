import io
import zipfile

from autopost import kittext
from autopost.drive import build_kit_item

TXT = """REEL 09 — DEMO — UPLOAD KIT
UPLOAD TIME (all 4 platforms): 25-12-2026, 7:00 AM IST
COPY-PASTE TEXT

━━ YOUTUBE SHORTS ━━
Title: Demo title #shorts
Description:
Line one.
Line two.
#shorts #demo

Keywords / Tags:
a, b, c
(Pinned comment: see PINNED COMMENTS section at the end)

━━ INSTAGRAM REELS ━━
Caption:
IG caption
#ig

━━ DAILYMOTION ━━
Title: DM title
Description:
DM text
Tags: x, y
Hashtags: #dm
(Add at the end: Made with AI voice.)

=====
PINNED COMMENTS
=====
━━ YOUTUBE SHORTS (pin it) ━━
Pin me
━━ INSTAGRAM REELS ━━
IG pin
"""


def test_free_layout_txt():
    k = kittext.parse(TXT)
    assert k["youtube"]["title"] == "Demo title #shorts"
    assert k["youtube"]["tags"] == ["a", "b", "c"]
    assert k["youtube"]["pinned"] == "Pin me"
    assert "Line two." in k["youtube"]["caption"]
    assert k["instagram"]["caption"].startswith("IG caption")
    assert k["instagram"]["pinned"] == "IG pin"
    dm = k["dailymotion"]
    assert dm["tags"] == ["x", "y"] and "#dm" in dm["caption"] and dm["caption"].endswith("Made with AI voice.")
    assert k["youtube"]["schedule"].startswith("2026-12-25T07:00")


def test_docx_and_md_and_html():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        body = "".join(f"<w:p><w:r><w:t>{l}</w:t></w:r></w:p>" for l in TXT.replace("&", "&amp;").splitlines())
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
    for name, data in (("kit.docx", buf.getvalue()), ("kit.md", TXT.encode()),
                       ("kit.html", TXT.replace("\n", "<br>").encode())):
        k = kittext.parse(kittext.to_text(name, data))
        assert k and k["youtube"]["title"] == "Demo title #shorts", name


def test_not_a_kit_and_candidates():
    assert kittext.parse("hello world") is None
    assert kittext.is_candidate({"name": "notes.txt"})
    assert not kittext.is_candidate({"name": "v.mp4"})
    assert not kittext.is_candidate({"name": "00_Text_YouTube_x.md"})


def test_kit_item_uses_one_thumb_everywhere():
    files = [{"id": "v", "name": "r.mp4", "videoMediaMetadata": {"width": 1080, "height": 1920, "durationMillis": 45000}},
             {"id": "t1", "name": "r-thumb-vertical.jpg"}, {"id": "t2", "name": "r-thumb-16x9.jpg"}]
    it = build_kit_item("z::", "pack", files, kittext.parse(TXT))[0]
    assert it["kind"] == "reel"
    assert {c["id"] for c in it["covers"].values()} == {"t1"} and len(it["covers"]) == 4

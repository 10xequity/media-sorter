"""Media Sorter: cut photos into a video (vertical for Reels, horizontal or square) from a template.

The dashboard's "Make a reel" uses this. From a terminal (or from Claude in chat):
  python reel.py --list
  python reel.py --template fast-cuts --tag hitting
  python reel.py --template countdown --event "2024-10-05 Showdown 2024" --pick random --fresh
  python reel.py --template highlights --shape horizontal --move pan

Reads library photos; writes one new .mp4 into exports/reels/. Originals are never changed. No text and no sound
are added: words and audio go on afterwards in Edits or Instagram.
Templates are in templates.json so they can be refreshed without touching code.
"""
import argparse, json, re, subprocess, time

import numpy as np
from PIL import Image, ImageFilter

import sorter as S

W, H, FPS = 1080, 1920, 30
SHAPES = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
FITS = {"fill": "crop", "pan": "crop", "whole": "blur"}  # "": whatever the template says


def templates():
    return json.loads((S.HERE / "templates.json").read_text(encoding="utf-8"))


def choose(t, ids=None, view=None, pick="best", fresh=False, ordered=False):
    """The photos for a reel: the ones you selected, or the best / a random set from a view of the library."""
    c = S.db()
    usable = ("kind='photo' AND err IS NULL AND dup_of IS NULL "
              "AND id NOT IN (SELECT media_id FROM tags WHERE tag='eyes closed')")
    if ids:
        w, a = f"id {S.IN}", [json.dumps(ids)]
    else:
        w, a = S.where({**(view or {}), "view": "active", "kind": "photo"})
        w += f" AND {usable} AND {S.ES} >= 2"
        if fresh:
            w += " AND id NOT IN (SELECT media_id FROM tags WHERE tag='posted')"
        if t["order"] == "sequence":  # one whole burst: the longest one in this view (or a random one)
            g = c.execute(f"SELECT grp FROM media WHERE {w} AND grp IS NOT NULL GROUP BY grp HAVING count(*) >= 6 "
                          f"ORDER BY {'random()' if pick == 'random' else 'count(*) DESC'} LIMIT 1", a).fetchone()
            if not g:
                return []
            w, a = w + " AND grp=?", [*a, g[0]]
    order = "taken, path" if ids or t["order"] == "sequence" else "random()" if pick == "random" else f"{S.ES} DESC, random()"
    rows = c.execute(f"SELECT id, path, taken, {S.ES} AS es FROM media WHERE {w} AND kind='photo' ORDER BY {order} LIMIT ?",
                     (*a, 60 if ids else t["photos"])).fetchall()
    if ids and ordered:  # the order you arranged them in the Project panel
        pos = {i: n for n, i in enumerate(ids)}
        return sorted(rows, key=lambda r: pos[r["id"]])
    key = (lambda r: (r["es"] or 0, r["taken"])) if t["order"] == "best-last" else (lambda r: (r["taken"], r["path"]))
    return sorted(rows, key=key)


def cover(im, w, h, fx=0.5, fy=0.5):
    """Scale and crop to fill w x h exactly, keeping the focus point as central as the edges allow."""
    s = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * s)), max(h, round(im.height * s))), Image.LANCZOS)
    l = min(max(0, round(fx * im.width - w / 2)), im.width - w)
    t = min(max(0, round(fy * im.height - h / 2)), im.height - h)
    return im.crop((l, t, l + w, t + h))


def still(row, fit, w, h):
    """A photo (with its saved look) scaled to cover a w x h frame, and the pixel the frame should centre on."""
    import ai
    im = ai.load(row["path"])
    if im is None:
        return None
    im = S.look(im, S.edit(row["id"])["look"])
    if fit == "blur":  # whole photo visible, on a blurred, darkened copy of itself
        out = cover(im, w, h).filter(ImageFilter.GaussianBlur(40)).point(lambda v: v * 0.55)
        im.thumbnail((w, h), Image.LANCZOS)
        out.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
        return out, w / 2, h / 2
    s = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * s)), max(h, round(im.height * s))), Image.LANCZOS)
    fx, fy = S.focus(row["id"])
    return im, fx * im.width, fy * im.height


def window(im, cx, cy, w, h):
    """The w x h box centred as near (cx, cy) as the picture's edges allow."""
    l, t = min(max(0, cx - w / 2), im.width - w), min(max(0, cy - h / 2), im.height - h)
    return (l, t, l + w, t + h)


def render(t, rows, name="", shape="vertical", move=""):
    """Write the video. Returns its path, or None if nothing usable was chosen.
    move "pan" starts each photo on its middle and slides to the action; "fill" and "whole" overrule the template's fit."""
    if not rows:
        return None
    W, H = SHAPES[shape]
    fit = FITS.get(move, t["fit"])
    out = S.EXPORTS / "reels"
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w\- ]", "", str(name)).strip()[:50] or t["id"]
    path = out / f"{name} {time.strftime('%Y-%m-%d %H%M%S')}.mp4"
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
                          stdin=subprocess.PIPE, creationflags=S.NOWIN)
    zoom, beats = t.get("zoom", 0), t["beats"]
    try:
        for loop in range(t.get("loops", 1)):
            for i, row in enumerate(rows):
                got = still(row, fit, round(W * (1 + zoom)), round(H * (1 + zoom)))
                if got is None:
                    continue
                im, cx, cy = got
                n = max(1, round(beats[min(i, len(beats) - 1)] * FPS))
                slide = move == "pan" and n >= FPS * 0.8  # shorter than that reads as a twitch: just frame the action
                if not zoom and not slide:
                    ff.stdin.write(im.resize((W, H), Image.BILINEAR, box=window(im, cx, cy, W, H)).tobytes() * n)
                    continue
                for k in range(n):  # slow push-in (the window shrinks) and, when sliding, a glide from the middle to the action
                    z, p = zoom * (1 - k / n), min(1, k / (n * 0.8))
                    p = p * p * (3 - 2 * p) if slide else 1
                    box = window(im, im.width / 2 + (cx - im.width / 2) * p, im.height / 2 + (cy - im.height / 2) * p,
                                 W * (1 + z), H * (1 + z))
                    ff.stdin.write(im.resize((W, H), Image.BILINEAR, box=box).tobytes())
    except Exception:
        ff.stdin.close()
        ff.wait()
        path.unlink(missing_ok=True)  # never leave a broken video behind
        raise
    ff.stdin.close()
    ff.wait()
    return path if path.exists() and path.stat().st_size else None


def make(template, ids=None, view=None, pick="best", fresh=False, name="", ordered=False, shape="vertical", move=""):
    t = next((x for x in templates()["templates"] if x["id"] == template), None)
    if not t or shape not in SHAPES or move not in ("", *FITS):
        raise ValueError("unknown template, shape or framing")
    rows = choose(t, ids, view, pick, fresh, ordered)
    path = render(t, rows, name, shape, move)
    if not path:
        raise ValueError("No usable photos here for this template. Try another section, or a different template.")
    return {"file": path.name, "path": str(path), "photos": len(rows), "ids": [r["id"] for r in rows]}


def selftest():
    assert cover(Image.new("RGB", (3000, 2000)), W, H, 0.9, 0.5).size == (W, H)
    assert cover(Image.new("RGB", (500, 2000)), W, H, 0.0, 1.0).size == (W, H)
    wide = Image.new("RGB", (3000, 1000))
    assert window(wide, 2900, 500, 1000, 1000) == (2000, 0, 3000, 1000) and window(wide, 0, 0, 1000, 1000) == (0, 0, 1000, 1000)
    grey = Image.new("RGB", (8, 8), (90, 100, 110))
    assert S.look(grey, {}) is grey and S.look(grey, {"filter": "bw"}).getpixel((0, 0))[0] == S.look(grey, {"filter": "bw"}).getpixel((0, 0))[2]
    assert S.look(grey, {"filter": "warm"}).getpixel((0, 0))[0] > 90 > 0 and S.look(grey, {"bright": 1.5}).getpixel((0, 0))[1] == 150
    assert S.clean_look({"bright": 9, "filter": "nope", "auto": 1, "x": 2}) == {"bright": 1.5, "auto": True}
    ids = [r[0] for r in S.db().execute("SELECT id FROM media WHERE kind='photo' AND status='active' AND err IS NULL LIMIT 3")]
    if ids:  # needs a scanned library; skipped on a fresh install
        t = {"id": "selftest", "photos": 3, "beats": [0.2], "zoom": 0.05, "fit": "blur", "order": "time"}
        p = render(t, choose(t, ids), "selftest")
        dur = float(S.ffrun(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))
        assert abs(dur - 0.6) < 0.1, dur
        p.unlink()
        p = render({**t, "beats": [1.0], "zoom": 0}, choose(t, ids), "selftest", "horizontal", "pan")
        assert S.ffrun(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height,nb_frames",
                        "-of", "csv=p=0", str(p)]).decode().strip() == "1920,1080,90"
        p.unlink()
    print("reel selftest passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--template", default="fast-cuts")
    for k in ("tag", "cat", "event", "q", "name"):
        ap.add_argument("--" + k, default="")
    ap.add_argument("--pick", choices=["best", "random"], default="best")
    ap.add_argument("--shape", choices=list(SHAPES), default="vertical")
    ap.add_argument("--move", choices=["", *FITS], default="", help="fill, pan (slide to the action) or whole photo")
    ap.add_argument("--fresh", action="store_true", help="skip photos already posted")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.list:
        for t in templates()["templates"]:
            print(f"{t['id']:14} {t['name']}: {t['about']}")
    else:
        view = {k: getattr(a, k) for k in ("tag", "cat", "event", "q") if getattr(a, k)}
        print(json.dumps(make(a.template, None, view, a.pick, a.fresh, a.name, shape=a.shape, move=a.move)))

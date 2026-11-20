"""Media Sorter: scan, score, group and review photos and videos.

Needs Python 3.12+, Pillow, numpy, and ffmpeg/ffprobe on PATH.
  python sorter.py            start the dashboard (http://localhost:8765)
  python sorter.py --scan     scan the folders in config.json, then exit
  python sorter.py --rescore  recalculate scores after changing "scoring" in config.json
  python sorter.py --selftest run the built-in checks
  python sorter.py --apply F  apply edits from a JSON file (the Claude round-trip; see README)
  python ai.py                the AI pass: tags, people, closed eyes, video dead time (see ai.py)
"""
import argparse, ctypes, hashlib, io, json, math, mimetypes, os, re, shutil, sqlite3
import subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import numpy as np
from PIL import Image, ImageOps

sys.modules.setdefault("sorter", sys.modules[__name__])  # ai.py imports this file by name, even when run directly
HERE = Path(__file__).parent
if not (HERE / "config.json").exists():  # new install or update: this computer's settings live in config.json,
    shutil.copyfile(HERE / "config.default.json", HERE / "config.json")  # which updates never overwrite
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
DATA = HERE / "data"
VERSION = (HERE / "VERSION").read_text().strip() if (HERE / "VERSION").exists() else "dev"
os.environ["PATH"] = str(HERE / "bin") + os.pathsep + os.environ.get("PATH", "")  # ffmpeg the installer put there
REVIEW = Path(CFG.get("review_dir") or HERE / "_Review")
EXPORTS = HERE / CFG.get("export_dir", "exports")
HOSTS = {"localhost", "127.0.0.1", *(h.lower() for h in CFG.get("allowed_hosts", []))}
ROOTS = [Path(r) for r in CFG["roots"]]
PHOTO, VIDEO = set(CFG["photo_ext"]), set(CFG["video_ext"])
SKIP_DIRS = {"site-packages", "node_modules", "venv", "__pycache__", "system volume information", "appdata"}
STOP_WORDS = {"img", "dsc", "dscn", "mov", "vid", "pxl", "gopr", "copy", "the", "and", "media", "finished",
              "drive", "downloads", "new", "folder", "final", "edit", "export"}
ES = "COALESCE(user_score, score)"  # effective score: your rating wins over the automatic one
# What lands in "Flagged": duplicates and scores of 0 or 1. B-roll is held to a lower bar: only a 0 flags it.
FLAG = f"({ES} <= CASE WHEN category='B-roll' THEN 0 ELSE 1 END OR dup_of IS NOT NULL)"
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# ponytail: one global write lock; fine for a single-user app, per-table locks if it ever bottlenecks
WLOCK = threading.RLock()
_local = threading.local()
scan_state = {"running": False, "done": 0, "total": 0, "errors": 0, "current": ""}
pending = {"running": False, "checked": None, "new": 0, "changed": 0, "folders": []}  # what a scan would pick up
update_state = {"latest": None}


def db():
    c = getattr(_local, "c", None)
    if c is None or getattr(_local, "data", None) != DATA:
        (DATA / "thumbs").mkdir(parents=True, exist_ok=True)
        (DATA / "previews").mkdir(exist_ok=True)
        c = sqlite3.connect(DATA / "library.db", timeout=30)
        c.row_factory = sqlite3.Row
        c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS media(
              id INTEGER PRIMARY KEY, path TEXT UNIQUE, orig_path TEXT, tkey TEXT, kind TEXT,
              size INTEGER, mtime REAL, taken TEXT, w INTEGER, h INTEGER, dur REAL, codec TEXT,
              score INTEGER, user_score INTEGER, metrics TEXT, dhash TEXT, fhash TEXT,
              dup_of INTEGER, grp INTEGER, event TEXT, category TEXT, status TEXT DEFAULT 'active', err TEXT);
            CREATE TABLE IF NOT EXISTS tags(media_id INTEGER, tag TEXT, src TEXT, PRIMARY KEY(media_id, tag));
            CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, ts REAL, label TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS ai(media_id INTEGER PRIMARY KEY, emb BLOB, dead TEXT);
            CREATE TABLE IF NOT EXISTS faces(id INTEGER PRIMARY KEY, media_id INTEGER, x REAL, y REAL, w REAL, h REAL,
              emb BLOB, blink REAL, person TEXT);
            CREATE TABLE IF NOT EXISTS roots(path TEXT PRIMARY KEY, serial TEXT, label TEXT);
            CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, at TEXT, secs REAL, folders TEXT,
              added INTEGER, changed INTEGER, missing INTEGER, errors INTEGER);
            CREATE INDEX IF NOT EXISTS i_face ON faces(media_id);
            CREATE INDEX IF NOT EXISTS i_status ON media(status, taken);
            CREATE INDEX IF NOT EXISTS i_tag ON tags(tag);
        """)
        _local.c, _local.data = c, DATA
    return c


# ---------- measuring quality ----------

def clamp(x):
    return max(0.0, min(1.0, float(x)))


def measure(im):
    """Raw numbers for one frame: sharpness, brightness, contrast, clipping, colourfulness."""
    im = im.convert("RGB")
    im.thumbnail((1600, 1600))
    a = np.asarray(im, dtype=np.float32)
    g = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    # Sharpness of the sharpest quarter of a 4x4 grid, so a crisp subject on a soft
    # background (the look you want) is not punished for the background.
    hh, ww = lap.shape[0] // 4, lap.shape[1] // 4
    tiles = sorted(lap[i * hh:(i + 1) * hh, j * ww:(j + 1) * ww].var() for i in range(4) for j in range(4))
    rg, yb = a[..., 0] - a[..., 1], (a[..., 0] + a[..., 1]) / 2 - a[..., 2]
    color = math.hypot(rg.std(), yb.std()) + 0.3 * math.hypot(rg.mean(), yb.mean())
    return {"lap": float(np.mean(tiles[-4:])), "mean": float(g.mean()), "std": float(g.std()),
            "clip": float(((g < 6) | (g > 249)).mean()), "color": float(color)}


def reduce(ms):
    """Collapse per-frame measurements into one set of raw numbers (kept so scores can be recalculated)."""
    raw = {k: float(np.median([m[k] for m in ms])) for k in ms[0]}
    raw["max_mean"], raw["max_std"] = max(m["mean"] for m in ms), max(m["std"] for m in ms)
    return raw


def rate(raw, w, h):
    """Turn raw measurements into a 0-5 score plus the sub-scores shown in the viewer."""
    S = CFG["scoring"]
    if raw["max_mean"] < S["dead_mean"] or raw["max_std"] < S["dead_std"]:
        return 0, {"focus": 0, "light": 0, "contrast": 0, "color": 0, "res": 0, "note": "nothing visible", "raw": raw}
    med = raw.get
    short = min(w or 0, h or 0)
    sub = {
        "focus": clamp((math.log10(med("lap") + 1) - S["lap_lo"]) / (S["lap_hi"] - S["lap_lo"])),
        "light": clamp(1 - abs(med("mean") - 120) / 120 * 1.1 - med("clip") * 1.5),
        "contrast": clamp(med("std") / S.get("contrast_full", 55)),
        "color": clamp(med("color") / S.get("color_full", 45)),
        "res": 1.0 if short >= 1080 else 0.8 if short >= 720 else 0.5 if short >= 480 else 0.3,
    }
    q = sum(sub[k] * v for k, v in S["weights"].items())
    if sub["focus"] < 0.15:  # badly out of focus can never score well
        q = min(q, 0.3)
    return 1 + sum(q >= t for t in S["cuts"]), {**{k: round(v, 2) for k, v in sub.items()}, "raw": raw}


def dhash(im):
    g = np.asarray(im.convert("L").resize((9, 8)), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "%016x" % int("".join("1" if b else "0" for b in bits), 2)


def fhash(path, size):
    # ponytail: hashes first+last 1 MB plus size, not the whole file; full hash if false matches ever show up
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    return h.hexdigest()


# ---------- reading files ----------

def ffrun(args, timeout=90):
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NOWIN).stdout


def grab(path, at=None):
    """One frame as a PIL image via ffmpeg (also the fallback decoder for HEIC photos)."""
    seek = ["-ss", "%.2f" % at] if at else []
    out = ffrun(["ffmpeg", "-v", "error", *seek, "-i", str(path), "-frames:v", "1",
                 "-vf", "scale=1600:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"])
    return Image.open(io.BytesIO(out)) if out else None


def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def path_tags(path):
    root = next((r for r in ROOTS if str(path).lower().startswith(str(r).lower())), path.parent)
    words = re.findall(r"[a-z]{3,}", str(path.relative_to(root).with_suffix("")).lower())
    return {w for w in words if w not in STOP_WORDS}


def analyse(path):
    """Everything the scan learns about one file. Runs in worker threads; never writes to the database."""
    path = Path(path)
    st = path.stat()
    kind = "photo" if path.suffix.lower() in PHOTO else "video"
    r = {"path": str(path), "kind": kind, "size": st.st_size, "mtime": st.st_mtime, "taken": fmt(st.st_mtime),
         "tkey": hashlib.sha1(str(path).lower().encode()).hexdigest()[:16], "dur": None, "codec": None,
         "w": None, "h": None, "score": None, "metrics": None, "dhash": None, "fhash": None, "err": None,
         "tags": path_tags(path)}
    try:
        r["fhash"] = fhash(path, st.st_size)
        frames = []
        if kind == "photo":
            try:
                im = Image.open(path)
                when = im.getexif().get_ifd(0x8769).get(36867)  # EXIF "date taken"
                if when:
                    r["taken"] = when[:10].replace(":", "-") + when[10:19]
                im = ImageOps.exif_transpose(im)
                r["w"], r["h"] = im.size
                im.draft("RGB", (1920, 1920))
            except Exception:
                im = grab(path)
                if im is None:
                    raise ValueError("cannot read image")
                r["w"], r["h"] = im.size
            frames = [im]
        else:
            info = json.loads(ffrun(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                                     "-show_streams", str(path)]) or b"{}")
            v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            if not v:
                raise ValueError("no video stream")
            r["w"], r["h"], r["codec"] = v.get("width"), v.get("height"), v.get("codec_name")
            r["dur"] = float(info.get("format", {}).get("duration") or 0) or None
            made = info.get("format", {}).get("tags", {}).get("creation_time")
            if made:
                try:
                    when = datetime.fromisoformat(made.replace("Z", "+00:00"))
                    if when.year >= 1990:  # many files carry a blank "1904" date; the file date is better
                        r["taken"] = when.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    pass
            spots = [r["dur"] * f for f in (0.1, 0.5, 0.9)] if r["dur"] else [None]
            frames = [f for f in (grab(path, t) for t in spots) if f is not None]
            if not frames:
                raise ValueError("no frames decoded")
        thumb = frames[len(frames) // 2].convert("RGB")
        r["dhash"] = dhash(thumb)
        r["score"], sub = rate(reduce([measure(f) for f in frames]), r["w"], r["h"])
        r["metrics"] = json.dumps(sub)
        thumb.thumbnail((480, 480))
        thumb.save(DATA / "thumbs" / (r["tkey"] + ".jpg"), quality=80)
        short = min(r["w"] or 0, r["h"] or 0)
        r["tags"] |= {"4k" if short >= 2160 else "hd" if short >= 720 else "sd",
                      "portrait" if (r["h"] or 0) > (r["w"] or 0) else "landscape"}
        if kind == "video" and r["dur"]:
            r["tags"].add("short clip" if r["dur"] < 15 else "long video" if r["dur"] > 300 else "clip")
        if r["score"] == 0:
            r["tags"].add("nothing visible")
    except Exception as e:  # a broken file must not stop the scan; it is listed with its error
        r["err"] = f"{type(e).__name__}: {e}"[:200]
    return r


# ---------- drives and scan folders ----------

def volume(path):
    """(serial, label) of the drive a folder is on. The serial identifies a drive even when its letter changes."""
    if os.name != "nt":  # ponytail: Macs mount drives by name under /Volumes, which is already stable
        return None, None
    label, serial = ctypes.create_unicode_buffer(261), ctypes.c_uint(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(os.path.splitdrive(str(path))[0] + "\\", label, 261,
                                                      ctypes.byref(serial), None, None, None, 0)
    return (f"{serial.value:08X}", label.value) if ok else (None, None)


def drives():
    if os.name == "nt":
        bits = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{chr(65 + i)}:\\" for i in range(26) if bits >> i & 1]
    vols = Path("/Volumes")
    return [str(Path.home()), *(str(v) for v in (vols.iterdir() if vols.is_dir() else []))]


def save_cfg():
    (HERE / "config.json").write_text(json.dumps(CFG, indent=2), encoding="utf-8")


def under(path, folder):
    """True if path is inside folder (or is it). Case-blind, as Windows and default Mac drives are."""
    p, f = str(path).lower(), str(folder).lower().rstrip("\\/")
    return p == f or p.startswith(f + os.sep)


def reconnect():
    """Remember which drive each scan folder is on. If a drive comes back under a new letter, follow it:
    the library keeps every tag, score and person for those files."""
    global ROOTS, REVIEW
    c, moved = db(), []
    with WLOCK:
        for i, r in enumerate(list(ROOTS)):
            if r.is_dir():
                c.execute("INSERT OR REPLACE INTO roots VALUES(?,?,?)", (str(r), *volume(r)))
                continue
            was = c.execute("SELECT serial FROM roots WHERE path=?", (str(r),)).fetchone()
            tail = os.path.splitdrive(str(r))[1].lstrip("\\/")
            new = was and was[0] and next((Path(d + tail) for d in drives()
                                           if volume(d)[0] == was[0] and Path(d + tail).is_dir()), None)
            if not new:
                continue  # drive not connected: its files stay in the library, shown as offline
            old_drive, new_drive = os.path.splitdrive(str(r))[0], os.path.splitdrive(str(new))[0]
            for col in ("path", "orig_path"):
                c.execute(f"UPDATE OR IGNORE media SET {col} = ? || substr({col}, ?) WHERE lower(substr({col}, 1, ?)) = ?",
                          (new_drive, len(old_drive) + 1, len(old_drive), old_drive.lower()))
            c.execute("DELETE FROM roots WHERE path=?", (str(r),))
            c.execute("INSERT OR REPLACE INTO roots VALUES(?,?,?)", (str(new), *volume(new)))
            if under(REVIEW, old_drive + os.sep):
                REVIEW = Path(new_drive + os.path.splitdrive(str(REVIEW))[1])
                CFG["review_dir"] = str(REVIEW)
            ROOTS[i] = new
            moved.append(f"{r} is now {new}")
        c.commit()
        if moved:
            CFG["roots"] = [str(r) for r in ROOTS]
            save_cfg()
    return moved


def root_status():
    c = db()
    out = []
    for r in ROOTS:
        row = c.execute("SELECT label FROM roots WHERE path=?", (str(r),)).fetchone()
        n = c.execute("SELECT count(*) FROM media WHERE status='active' AND lower(substr(path, 1, ?)) = ?",
                      (len(str(r)), str(r).lower())).fetchone()[0]
        out.append({"path": str(r), "label": row[0] if row else None, "online": r.is_dir(), "files": n})
    return out


def set_roots(paths):
    """Choose which folders get scanned. Files already in the library from other folders are kept."""
    global ROOTS, REVIEW
    new = []
    for p in paths:
        p = Path(str(p))
        if not p.is_dir():
            raise ValueError(f"Not a folder: {p}")
        if not any(under(p, q) for q in new):  # a folder inside one already chosen adds nothing
            new = [q for q in new if not under(q, p)] + [p]
    ROOTS = new
    CFG["roots"] = [str(r) for r in new]
    if new and not CFG.get("review_dir"):  # beside the photos, on the same drive, so moving files there is instant
        first = new[0]
        base = first if first.parent == first or str(first.parent) in ("/", "/Volumes") else first.parent
        REVIEW = base / "_Review"
        CFG["review_dir"] = str(REVIEW)
    save_cfg()
    reconnect()
    threading.Thread(target=check, daemon=True).start()
    return root_status()


def tree(path):
    """The folders inside a folder (or the drives, for an empty path), for the scan-folder picker."""
    if not path:
        return {"path": "", "dirs": [{"path": d, "name": f"{d}  {volume(d)[1] or ''}".strip()} for d in drives()]}
    p = Path(path)
    try:
        dirs = sorted((x for x in p.iterdir() if x.is_dir() and x.name[0] not in ".$"
                       and x.name.lower() not in SKIP_DIRS), key=lambda x: x.name.lower())
    except OSError as e:
        raise ValueError(f"Can't open {p}: {e.strerror or e}")
    return {"path": str(p), "dirs": [{"path": str(d), "name": d.name} for d in dirs]}


def changed_files():
    """(files a scan would read, every file seen, folders that are connected). Listing only; nothing is opened."""
    c = db()
    known = {r["path"]: (r["size"], r["mtime"]) for r in c.execute("SELECT path,size,mtime FROM media")}
    todo, seen = [], set()
    for p, is_photo in walk():
        seen.add(p)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if is_photo and st.st_size < CFG["min_photo_kb"] * 1024:
            continue  # icons and web scraps
        if known.get(p) != (st.st_size, st.st_mtime):
            todo.append((p, p in known))
    return todo, seen, known


def check():
    """On start-up and after folder changes: count what is new or changed, so the dashboard can ask before scanning."""
    if pending["running"] or scan_state["running"]:
        return
    pending["running"] = True
    try:
        reconnect()
        todo, _, _ = changed_files()
        by = {}
        for p, was_known in todo:
            by[os.path.dirname(p)] = by.get(os.path.dirname(p), 0) + 1
        pending.update(new=sum(not k for _, k in todo), changed=sum(k for _, k in todo),
                       folders=sorted(by.items(), key=lambda x: -x[1])[:50], checked=time.strftime("%Y-%m-%d %H:%M"))
    finally:
        pending["running"] = False


def walk():
    for root in ROOTS:
        if not root.is_dir():
            continue  # drive not connected
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x.lower() not in SKIP_DIRS and x[0] not in ".$"
                       and Path(d, x) != REVIEW]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in PHOTO or ext in VIDEO:
                    yield os.path.join(d, f), ext in PHOTO


def scan():
    if scan_state["running"]:
        return
    scan_state.update(running=True, done=0, total=0, errors=0, current="Listing files")
    try:
        c, t0 = db(), time.time()
        reconnect()
        listed, seen, known = changed_files()
        todo = [p for p, _ in listed]
        scan_state["total"] = len(todo)
        cols = "path,orig_path,tkey,kind,size,mtime,taken,w,h,dur,codec,score,metrics,dhash,fhash,event,err"
        with ThreadPoolExecutor(CFG["workers"]) as ex:
            for r in ex.map(analyse, todo):
                r["orig_path"] = r["path"]
                r["event"] = f"{r['taken'][:10]} {Path(r['path']).parent.name}"
                with WLOCK:
                    c.execute(f"INSERT INTO media({cols}) VALUES({','.join(':' + k for k in cols.split(','))}) "
                              "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                              "taken=excluded.taken, w=excluded.w, h=excluded.h, dur=excluded.dur, codec=excluded.codec, "
                              "score=excluded.score, metrics=excluded.metrics, dhash=excluded.dhash, "
                              "fhash=excluded.fhash, tkey=excluded.tkey, err=excluded.err", r)
                    mid = c.execute("SELECT id FROM media WHERE path=?", (r["path"],)).fetchone()[0]
                    c.execute("DELETE FROM tags WHERE media_id=? AND src='auto'", (mid,))
                    c.execute("DELETE FROM ai WHERE media_id=?", (mid,))  # changed file: the AI pass redoes it
                    c.executemany("INSERT OR IGNORE INTO tags VALUES(?,?,'auto')", [(mid, t) for t in r["tags"]])
                    c.commit()
                scan_state["done"] += 1
                scan_state["errors"] += bool(r["err"])
                scan_state["current"] = os.path.basename(r["path"])
        with WLOCK:  # files that vanished from disk since the last scan
            online = [r for r in ROOTS if r.is_dir()]  # an unplugged drive's files are offline, not gone
            gone = [p for p in known if p not in seen and any(under(p, r) for r in online)]
            n = sum(c.execute("UPDATE media SET status='missing' WHERE path=? AND status='active'", (p,)).rowcount for p in gone)
            c.executemany("UPDATE media SET status='active' WHERE path=? AND status='missing'", [(p,) for p in seen])
            c.execute("INSERT INTO scans(at,secs,folders,added,changed,missing,errors) VALUES(?,?,?,?,?,?,?)",
                      (time.strftime("%Y-%m-%d %H:%M"), round(time.time() - t0, 1), json.dumps([str(r) for r in online]),
                       sum(not k for _, k in listed), sum(k for _, k in listed), n, scan_state["errors"]))
            c.commit()
        pending.update(new=0, changed=0, folders=[], checked=time.strftime("%Y-%m-%d %H:%M"))
        scan_state["current"] = "Grouping"
        regroup()
    finally:
        scan_state.update(running=False, current="")


def regroup():
    """Mark exact duplicates and keep shots taken together (bursts, action sequences) in one group."""
    c = db()
    with WLOCK:
        rows = c.execute("SELECT id,path,kind,taken,dur,w,h,fhash,dhash FROM media "
                         "WHERE status='active' AND err IS NULL ORDER BY taken, path").fetchall()
        c.execute("UPDATE media SET dup_of=NULL, grp=NULL WHERE status='active'")
        keep = {}
        for r in sorted(rows, key=lambda r: (len(r["path"]), r["path"])):  # shortest name is the keeper
            # ponytail: duplicate = identical file, or identical picture taken the same second (a re-saved copy).
            # Resized or re-encoded copies with new timestamps are not caught; perceptual matching in stage 2.
            flat = r["dhash"] in ("0" * 16, "f" * 16)  # featureless frames all look alike; never match on those
            for key in (("f", r["fhash"]), ("d", r["id"] if flat else r["dhash"], r["taken"], r["kind"])):
                if key in keep and keep[key] != r["id"]:
                    c.execute("UPDATE media SET dup_of=? WHERE id=?", (keep[key], r["id"]))
                    break
                keep.setdefault(key, r["id"])
        # Near-duplicates: the same picture at another size or in another folder (resized or re-exported copy).
        # Burst frames are never matched: they share a folder and a size.
        dup = {r[0] for r in c.execute("SELECT id FROM media WHERE dup_of IS NOT NULL")}
        ph = [r for r in rows if r["kind"] == "photo" and r["w"] and r["h"] and r["id"] not in dup
              and r["dhash"] not in ("0" * 16, "f" * 16)]
        if ph:
            bits = np.unpackbits(np.array([int(r["dhash"], 16) for r in ph], dtype=">u8").view(np.uint8)
                                 .reshape(-1, 8), axis=1).astype(np.float32)
            best = lambda r: (-r["w"] * r["h"], len(r["path"]), r["path"])  # biggest picture is the keeper
            for i in range(0, len(ph), 2000):  # in slices, so memory stays flat on big libraries
                b = bits[i:i + 2000]
                far = b @ (1 - bits.T) + (1 - b) @ bits.T  # number of differing bits
                for x, y in zip(*np.nonzero(far <= CFG.get("near_dup_bits", 2))):
                    p, q = ph[i + x], ph[y]
                    if i + x < y and abs(p["w"] / p["h"] - q["w"] / q["h"]) < 0.02 and (
                            (p["w"], p["h"]) != (q["w"], q["h"]) or os.path.dirname(p["path"]) != os.path.dirname(q["path"])):
                        keep_, drop = sorted((p, q), key=best)
                        c.execute("UPDATE media SET dup_of=? WHERE id=? AND dup_of IS NULL AND "
                                  "(SELECT dup_of FROM media WHERE id=?) IS NULL", (keep_["id"], drop["id"], keep_["id"]))
        gap = CFG["burst_gap_seconds"]
        t = lambda s: (datetime.strptime(s, "%Y-%m-%d %H:%M:%S") - datetime(1970, 1, 1)).total_seconds()
        groups, last = [], {}
        for r in rows:
            folder = os.path.dirname(r["path"])
            start = t(r["taken"])
            if folder in last and start - last[folder][0] <= gap:
                last[folder][1].append(r)
            else:
                last[folder] = [start, [r]]
                groups.append(last[folder][1])
            last[folder][0] = start + (r["dur"] or 0)
        c.execute("DELETE FROM tags WHERE tag='stop-motion candidate' AND src='auto'")
        for g in groups:
            if len(g) > 1:
                ids = json.dumps([r["id"] for r in g])
                c.execute("UPDATE media SET grp=? WHERE id IN (SELECT value FROM json_each(?))", (g[0]["id"], ids))
                if sum(r["kind"] == "photo" for r in g) >= CFG["stop_motion_min_photos"]:
                    c.executemany("INSERT OR IGNORE INTO tags VALUES(?, 'stop-motion candidate', 'auto')",
                                  [(r["id"],) for r in g if r["kind"] == "photo"])
        c.commit()


def rescore():
    """Recalculate every automatic score from stored measurements after editing "scoring" in config.json."""
    c = db()
    with WLOCK:
        rows = c.execute("SELECT id,w,h,metrics FROM media WHERE metrics IS NOT NULL").fetchall()
        for r in rows:
            score, sub = rate(json.loads(r["metrics"])["raw"], r["w"], r["h"])
            c.execute("UPDATE media SET score=?, metrics=? WHERE id=?", (score, json.dumps(sub), r["id"]))
        c.commit()
    return len(rows)


# ---------- changes you make (all undoable except emptying to the Recycle Bin) ----------

IN = "IN (SELECT value FROM json_each(?))"


def snapshot(c, ids, label):
    j = json.dumps(ids)
    snap = {"rows": [dict(r) for r in c.execute(
                f"SELECT id,path,status,user_score,category,event FROM media WHERE id {IN}", (j,))],
            "tags": [tuple(r) for r in c.execute(f"SELECT media_id,tag,src FROM tags WHERE media_id {IN}", (j,))]}
    c.execute("INSERT INTO actions(ts,label,payload) VALUES(?,?,?)", (time.time(), label, json.dumps(snap)))


def update(ids, fields=None, add=(), remove=(), snap=True):
    fields = {k: v for k, v in (fields or {}).items() if k in ("user_score", "category", "event")}
    if fields.get("user_score") is not None and fields.get("user_score") not in range(6):
        fields.pop("user_score", None)
    for k in ("category", "event"):
        if k in fields:
            fields[k] = (str(fields[k]).strip()[:120] or None) if fields[k] else None
    clean = lambda ts: [t for t in (str(x).strip().lower()[:40] for x in ts) if t]
    add, remove = clean(add), clean(remove)
    c, j = db(), json.dumps(ids)
    with WLOCK:
        if snap:
            snapshot(c, ids, "edit")
        if fields:
            c.execute(f"UPDATE media SET {','.join(k + '=?' for k in fields)} WHERE id {IN}", (*fields.values(), j))
        for t in add:
            c.execute("INSERT OR IGNORE INTO tags SELECT value, ?, 'you' FROM json_each(?)", (t, j))
        for t in remove:
            c.execute(f"DELETE FROM tags WHERE tag=? AND media_id {IN}", (t, j))
        c.commit()
    return len(ids)


def apply_edits(path):
    """Apply a list of edits written by Claude (or by hand). One Undo reverses the whole batch.
    Each item: {"id": 12, "add_tags": [], "remove_tags": [], "category": "Action", "score": 4, "event": "..."}"""
    items = [it for it in json.loads(Path(path).read_text(encoding="utf-8")) if isinstance(it.get("id"), int)]
    c = db()
    with WLOCK:
        snapshot(c, [it["id"] for it in items], "import from Claude")
        c.commit()
        for it in items:
            fields = {k: it[src] for k, src in (("category", "category"), ("user_score", "score"), ("event", "event")) if src in it}
            update([it["id"]], fields, it.get("add_tags", ()), it.get("remove_tags", ()), snap=False)
    return len(items)


def cut(src, keep, out):
    """Write a new video containing only the kept stretches. The original is never touched."""
    sel = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in keep)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                    "-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB", "-af", f"aselect='{sel}',asetpts=N/SR/TB",
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-movflags", "+faststart", str(out)], capture_output=True, timeout=3600, creationflags=NOWIN)
    return out.exists() and out.stat().st_size > 0


def export(ids, name="", trim=False):
    """Copy the chosen files into one numbered folder for CapCut, Edits or Canva, with a manifest Claude can read.
    Copies only: the library is not changed."""
    name = re.sub(r"[^\w\- ]", "", str(name)).strip()[:60] or time.strftime("export %Y-%m-%d %H%M")
    dest = EXPORTS / name
    dest.mkdir(parents=True, exist_ok=True)
    c = db()
    rows = c.execute(f"SELECT {LIST_COLS} FROM media WHERE status='active' AND id {IN} ORDER BY taken, path",
                     (json.dumps(ids),)).fetchall()
    manifest = []
    for n, r in enumerate(rows, 1):
        out = dest / f"{n:03d}_{os.path.basename(r['path'])}"
        dead = c.execute("SELECT dead FROM ai WHERE media_id=?", (r["id"],)).fetchone()
        dead = json.loads(dead[0])["dead"] if dead and dead[0] else []
        trimmed = False
        if trim and r["kind"] == "video" and dead and r["dur"]:
            edges = [0.0, *(t for seg in dead for t in seg), r["dur"]]
            keep = [(a, b) for a, b in zip(edges[::2], edges[1::2]) if b - a > 0.5]
            trimmed = bool(keep) and cut(r["path"], keep, out.with_suffix(".mp4"))
        if trimmed:
            out = out.with_suffix(".mp4")
        else:
            try:
                shutil.copy2(r["path"], out)
            except OSError:
                continue
        manifest.append({"id": r["id"], "file": out.name, "original": r["path"], "kind": r["kind"], "taken": r["taken"],
                         "score": r["es"], "category": r["category"], "event": r["event"], "trimmed": trimmed,
                         "width": r["w"], "height": r["h"], "seconds": r["dur"], "tags": (r["tags"] or "").split("|")})
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (dest / "README for Claude.md").write_text(f"""# Media Sorter export: {name}

{len(manifest)} files copied from the library, numbered in the order they were taken. The originals are untouched.
`manifest.json` lists each file with its library id, tags, score, category and event.

To send changes back to the library, write `apply.json` in this folder as a list like

    [{{"id": 12, "add_tags": ["hero shot"], "remove_tags": [], "category": "Action", "score": 5, "event": "Showdown 2024"}}]

(every key except `id` is optional) and run, from {HERE}:

    python sorter.py --apply "{dest / 'apply.json'}"

One Undo in the dashboard reverses the whole import.
""", encoding="utf-8")
    return {"folder": str(dest), "files": len(manifest), "trimmed": sum(m["trimmed"] for m in manifest)}


def relocate(ids, to_review):
    """Move files into the review folder, or back where they came from. Returns how many moved."""
    c, moved = db(), 0
    with WLOCK:
        snapshot(c, ids, "move to review" if to_review else "restore")
        want = "status='active'" if to_review else "status LIKE 'review%'"
        for r in c.execute(f"SELECT id,path,orig_path,dup_of FROM media WHERE {want} AND id {IN}",
                           (json.dumps(ids),)).fetchall():
            bucket = "duplicates" if r["dup_of"] else "rejects"
            dest = str(REVIEW / bucket / f"{r['id']}_{os.path.basename(r['orig_path'])}") if to_review else r["orig_path"]
            try:
                if os.path.exists(dest):
                    raise FileExistsError(dest)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(r["path"], dest)
            except OSError:
                continue  # in use, missing, or name taken: leave it exactly where it is
            c.execute("UPDATE media SET path=?, status=? WHERE id=?",
                      (dest, f"review_{bucket}" if to_review else "active", r["id"]))
            moved += 1
        c.commit()
    return moved


def undo():
    c = db()
    with WLOCK:
        a = c.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 1").fetchone()
        if not a:
            return None
        snap = json.loads(a["payload"])
        ids = []
        for r in snap["rows"]:
            cur = c.execute("SELECT path,status FROM media WHERE id=?", (r["id"],)).fetchone()
            if not cur or cur["status"] == "deleted":
                continue  # already sent to the Recycle Bin; restore it from there
            if cur["path"] != r["path"]:
                try:
                    os.makedirs(os.path.dirname(r["path"]), exist_ok=True)
                    shutil.move(cur["path"], r["path"])
                except OSError:
                    continue
            c.execute("UPDATE media SET path=?,status=?,user_score=?,category=?,event=? WHERE id=?",
                      (r["path"], r["status"], r["user_score"], r["category"], r["event"], r["id"]))
            ids.append(r["id"])
        c.execute(f"DELETE FROM tags WHERE media_id {IN}", (json.dumps(ids),))
        c.executemany("INSERT OR IGNORE INTO tags VALUES(?,?,?)", [t for t in snap["tags"] if t[0] in set(ids)])
        c.execute("DELETE FROM actions WHERE id=?", (a["id"],))
        c.commit()
    return a["label"]


def show(path):
    """Open a file or folder in this computer's normal app (Explorer / Finder, photo viewer, player)."""
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.run(["open", str(path)], timeout=30)


def recycle(path):
    """Send a file to the Recycle Bin (Windows) or the Trash (Mac), recoverable there. True on success."""
    if os.name != "nt":  # via Finder, so "Put Back" works; the path goes in as an argument, never as script text
        subprocess.run(["osascript", "-e", "on run argv", "-e", "set f to POSIX file (item 1 of argv) as alias",
                        "-e", 'tell application "Finder" to delete f', "-e", "end run", str(path)],
                       capture_output=True, timeout=60)
        return not os.path.exists(path)
    class Op(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAborted", ctypes.c_int),
                    ("hMap", ctypes.c_void_p), ("title", ctypes.c_void_p)]
    buf = ctypes.create_unicode_buffer(str(path) + "\0")  # the API wants a double-null-terminated list
    op = Op(None, 3, ctypes.cast(buf, ctypes.c_void_p), None, 0x40 | 0x10 | 0x4 | 0x400, 0, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not os.path.exists(path)


def delete(ids):
    """Only files already sitting in the review folder can be deleted, and only to the Recycle Bin."""
    c, n = db(), 0
    with WLOCK:
        for r in c.execute(f"SELECT id,path FROM media WHERE status LIKE 'review%' AND id {IN}",
                           (json.dumps(ids),)).fetchall():
            if Path(r["path"]).is_relative_to(REVIEW) and recycle(r["path"]):
                c.execute("UPDATE media SET status='deleted' WHERE id=?", (r["id"],))
                n += 1
        c.commit()
    return n


# ---------- reading the library ----------

def where(p):
    view = p.get("view", "active")
    w = ["status LIKE 'review%'" if view == "review" else
         f"status='active' AND {FLAG}" if view == "flagged" else "status='active'"]
    a = []
    for key, sql in (("kind", "kind=?"), ("cat", "category=?"), ("event", "event=?"), ("grp", "grp=?"),
                     ("min", f"{ES}>=?"), ("score", f"{ES}=?"),
                     ("tag", "id IN (SELECT media_id FROM tags WHERE tag=?)")):
        if p.get(key):
            w.append(sql)
            a.append(p[key])
    for tok in p.get("q", "").lower().split()[:8]:
        w.append("(lower(path) LIKE ? OR lower(event) LIKE ? OR lower(category) LIKE ? "
                 "OR id IN (SELECT media_id FROM tags WHERE tag LIKE ?))")
        a += [f"%{tok}%"] * 4
    return " AND ".join(w), a


SORTS = {"date": "taken DESC, path", "oldest": "taken, path", "score": f"{ES} DESC, taken DESC",
         "worst": f"{ES}, taken DESC", "size": "size DESC", "name": "path"}
LIST_COLS = (f"id,kind,path,w,h,dur,size,taken,score,user_score,{ES} AS es,category,event,grp,dup_of,status,err,"
             "(SELECT group_concat(tag, '|') FROM tags WHERE media_id=media.id) AS tags")


def look_alike(p, total):
    """Ids ranked by how well the picture matches the search words: used when you ask for it ("Best match")
    or when the ordinary search finds nothing. None when it does not apply or the AI pass has not run."""
    if not p.get("q") or (total and p.get("sort") != "match"):
        return None
    import ai
    if not ai.search_ready():
        return None
    return ai.search(db(), p["q"], *where({**p, "q": ""})) or None


def listing(p):
    w, a = where(p)
    c = db()
    total = c.execute(f"SELECT count(*) FROM media WHERE {w}", a).fetchone()[0]
    ids = look_alike(p, total)
    if ids:
        page = ids[int(p.get("offset", 0)):][:min(int(p.get("limit", 120)), 500)]
        got = {r["id"]: dict(r) for r in c.execute(f"SELECT {LIST_COLS} FROM media WHERE id {IN}", (json.dumps(page),))}
        return {"total": len(ids), "items": [got[i] for i in page], "smart": True}
    rows = c.execute(f"SELECT {LIST_COLS} FROM media WHERE {w} ORDER BY {SORTS.get(p.get('sort'), SORTS['date'])} "
                     "LIMIT ? OFFSET ?", (*a, min(int(p.get("limit", 120)), 500), int(p.get("offset", 0)))).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


def stats():
    c = db()
    one = lambda sql: c.execute(sql).fetchone()[0] or 0
    pairs = lambda sql: [list(r) for r in c.execute(sql)]
    act = "FROM media WHERE status='active'"
    return {
        "photos": one(f"SELECT count(*) {act} AND kind='photo'"),
        "videos": one(f"SELECT count(*) {act} AND kind='video'"),
        "bytes": one(f"SELECT sum(size) {act}"),
        "hours": one(f"SELECT sum(dur) {act}") / 3600,
        "flagged": one(f"SELECT count(*) {act} AND {FLAG}"),
        "dups": one(f"SELECT count(*) {act} AND dup_of IS NOT NULL"),
        "review": one("SELECT count(*) FROM media WHERE status LIKE 'review%'"),
        "review_bytes": one("SELECT sum(size) FROM media WHERE status LIKE 'review%'"),
        "unreadable": one(f"SELECT count(*) {act} AND err IS NOT NULL"),
        "scores": pairs(f"SELECT {ES}, count(*) {act} AND err IS NULL GROUP BY 1"),
        "categories": pairs(f"SELECT category, count(*) {act} AND category IS NOT NULL GROUP BY 1"),
        "events": pairs(f"SELECT event, count(*), sum(kind='photo'), sum(kind='video') {act} "
                        "GROUP BY 1 ORDER BY max(taken) DESC LIMIT 300"),
        "tags": pairs("SELECT tag, count(*) FROM tags JOIN media ON id=media_id AND status='active' "
                      "GROUP BY 1 ORDER BY 2 DESC LIMIT 40"),
        "people": pairs("SELECT tag, count(*) FROM tags JOIN media ON id=media_id AND status='active' "
                        "WHERE src='face' AND tag!='eyes closed' GROUP BY 1 ORDER BY 2 DESC LIMIT 80"),
        "ai_tags": pairs("SELECT tag, count(*) FROM tags JOIN media ON id=media_id AND status='active' "
                         "WHERE src IN ('ai','video') GROUP BY 1 ORDER BY 2 DESC"),
        "posted": pairs("SELECT tag, count(*) FROM tags JOIN media ON id=media_id AND status='active' "
                        "WHERE src='posted' GROUP BY 1 ORDER BY tag"),
        "eyes": one("SELECT count(*) FROM tags JOIN media ON id=media_id AND status='active' WHERE tag='eyes closed'"),
        "ai_todo": one(f"SELECT count(*) {act} AND err IS NULL AND id NOT IN (SELECT media_id FROM ai)"),
        "ai": sys.modules["ai"].state if "ai" in sys.modules else {"running": False},
        "all_categories": CFG["categories"],
        "can_undo": one("SELECT count(*) FROM actions"),
        "scan": scan_state,
        "pending": pending,
        "roots": root_status(),
        "version": VERSION,
        "latest": update_state["latest"],
    }


def item(mid):
    c = db()
    r = c.execute(f"SELECT {LIST_COLS}, metrics, orig_path, codec FROM media WHERE id=?", (mid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["faces"] = [dict(x) for x in c.execute("SELECT person, blink FROM faces WHERE media_id=?", (mid,))]
    dead = c.execute("SELECT dead FROM ai WHERE media_id=?", (mid,)).fetchone()
    d["dead"] = json.loads(dead[0]) if dead and dead[0] else None
    # "Suggested": the rest of its sequence first, then the best-scoring shots from the same event
    d["related"] = [dict(x) for x in c.execute(
        f"SELECT id,kind,{ES} AS es FROM media WHERE status='active' AND id!=? AND (grp=? OR event=?) "
        f"ORDER BY (grp IS ?) DESC, {ES} DESC LIMIT 12", (mid, r["grp"] or -1, r["event"], r["grp"]))]
    return d


PLAYABLE = {"h264", "vp8", "vp9", "av1"}
PLAYLOCK = threading.Lock()  # ponytail: one conversion at a time; per-file locks if several people watch at once


def playable(row):
    """The file itself if a browser can play it, otherwise a cached H.264 copy made on first viewing."""
    if row["codec"] in PLAYABLE and Path(row["path"]).suffix.lower() in (".mp4", ".m4v", ".mov", ".webm"):
        return Path(row["path"])
    out = DATA / "play" / (row["tkey"] + ".mp4")
    with PLAYLOCK:
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", row["path"], "-vf", "scale=-2:'min(1080,ih)'",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-movflags", "+faststart", str(tmp)],
                           capture_output=True, timeout=3600, creationflags=NOWIN)
            if tmp.exists() and tmp.stat().st_size:
                tmp.replace(out)
    return out if out.exists() else None


def preview(row):
    """A small silent 6-second clip for hover autoplay; made once, then cached."""
    out = DATA / "previews" / (row["tkey"] + ".mp4")
    if not out.exists():
        tmp = out.with_suffix(".tmp.mp4")
        start = "%.2f" % ((row["dur"] or 0) * 0.3)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", start, "-t", "6", "-i", row["path"], "-an",
                        "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)],
                       capture_output=True, timeout=120, creationflags=NOWIN)
        if tmp.exists() and tmp.stat().st_size:
            tmp.replace(out)
    return out if out.exists() else None


# ---------- web server (this computer only) ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype, cache=True):
        try:
            size = os.path.getsize(path)
        except OSError:
            return self.send_json({"error": "file not found"}, 404)
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start, end = int(m.group(1)), min(int(m.group(2) or end), end)
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 18, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionError, OSError):
                pass  # the browser stopped listening (scrolled away, seeked)

    def local_only(self):
        # Refuse anything not addressed to this machine by name; stops other websites reaching the app.
        host = (self.headers.get("Host") or "").split(":")[0]
        if host.lower() not in HOSTS:
            self.send_json({"error": "local access only"}, 403)
            return False
        return True

    def do_GET(self):
        if not self.local_only():
            return
        u = urlparse(self.path)
        p = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = u.path.strip("/").split("/")
        try:
            if u.path == "/":
                return self.send_file(HERE / "index.html", "text/html; charset=utf-8", cache=False)
            if u.path == "/api/media":
                return self.send_json(listing(p))
            if u.path == "/api/ids":
                w, a = where(p)
                ids = [r[0] for r in db().execute(f"SELECT id FROM media WHERE {w}", a)]
                return self.send_json(look_alike(p, len(ids)) or ids)
            if u.path == "/api/stats":
                return self.send_json(stats())
            if u.path == "/api/tree":
                return self.send_json(tree(p.get("path", "")))
            if u.path == "/api/scans":
                return self.send_json([dict(r) for r in db().execute("SELECT * FROM scans ORDER BY id DESC LIMIT 30")])
            if u.path == "/api/templates":
                import reel
                return self.send_json(reel.templates())
            if len(parts) == 2 and parts[0] == "reel":  # a finished reel, by file name only (no folders)
                f = EXPORTS / "reels" / os.path.basename(unquote(parts[1]))
                if f.suffix == ".mp4" and f.is_file():
                    return self.send_file(f, "video/mp4", cache=False)
            if u.path == "/api/item":
                d = item(int(p.get("id", 0)))
                return self.send_json(d or {"error": "not found"}, 200 if d else 404)
            if len(parts) == 2 and parts[0] in ("thumb", "file", "preview", "play") and parts[1].isdigit():
                r = db().execute("SELECT path,tkey,dur,kind,codec FROM media WHERE id=?", (int(parts[1]),)).fetchone()
                if r and r["kind"] == "video" and parts[0] == "play":
                    out = playable(r)
                    return self.send_file(out, mimetypes.guess_type(str(out))[0] or "video/mp4") if out \
                        else self.send_json({"error": "this video could not be converted for playback"}, 415)
                if r and parts[0] == "thumb":
                    return self.send_file(DATA / "thumbs" / (r["tkey"] + ".jpg"), "image/jpeg")
                if r and parts[0] == "file":
                    return self.send_file(r["path"], mimetypes.guess_type(r["path"])[0] or "application/octet-stream")
                if r and r["kind"] == "video" and (out := preview(r)):
                    return self.send_file(out, "video/mp4")
            self.send_json({"error": "not found"}, 404)
        except (ValueError, sqlite3.Error) as e:
            self.send_json({"error": str(e)}, 400)

    def do_POST(self):
        if not self.local_only():
            return
        # Browsers will not let another site send this header, so only the dashboard itself can change things.
        if self.headers.get("X-Sorter") != "1":
            return self.send_json({"error": "forbidden"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}") if 0 < n < 20_000_000 else {}
            ids = [int(i) for i in b.get("ids", [])]
            path = urlparse(self.path).path
            if path == "/api/update":
                out = {"changed": update(ids, b.get("set"), b.get("add", []), b.get("remove", []))}
            elif path == "/api/review":
                out = {"moved": relocate(ids, True)}
            elif path == "/api/restore":
                out = {"moved": relocate(ids, False)}
            elif path == "/api/delete":
                out = {"deleted": delete(ids)}
            elif path == "/api/export":
                out = export(ids, b.get("name", ""), bool(b.get("trim")))
                if self.client_address[0] == "127.0.0.1" and not self.headers.get("X-Forwarded-For"):
                    show(out["folder"])  # show the folder, but only to someone sitting at this computer
            elif path == "/api/reel":
                import reel
                view = {k: str(v) for k, v in (b.get("view") or {}).items() if k in ("cat", "event", "grp", "tag", "q", "score")}
                out = reel.make(str(b.get("template", "")), ids, view, "random" if b.get("pick") == "random" else "best",
                                bool(b.get("fresh")), b.get("name", ""))
            elif path == "/api/undo":
                out = {"undone": undo()}
            elif path == "/api/scan":
                threading.Thread(target=scan, daemon=True).start()
                out = {"started": True}
            elif path == "/api/roots":
                out = {"roots": set_roots(b.get("roots", []))}
            elif path == "/api/check":
                threading.Thread(target=check, daemon=True).start()
                out = {"started": True}
            elif path == "/api/upgrade":
                import update as updater
                out = updater.apply(b.get("zip") or None)
                self.send_json(out)
                if out.get("updated"):  # start the new version, then step aside for it
                    subprocess.Popen([sys.executable, str(HERE / "sorter.py"), "--retry"], cwd=HERE, creationflags=NOWIN)
                    threading.Timer(0.3, os._exit, (0,)).start()
                return
            elif path == "/api/quit":
                self.send_json({"bye": True})
                threading.Timer(0.3, os._exit, (0,)).start()  # after the reply has gone out
                return
            elif path == "/api/ai":
                import ai
                threading.Thread(target=ai.run, daemon=True).start()
                out = {"started": True}
            elif path == "/api/person":
                import ai
                out = {"renamed": ai.rename_person(str(b.get("old", "")), b.get("new", ""))}
            elif path == "/api/open" and ids:
                r = db().execute("SELECT path FROM media WHERE id=?", (ids[0],)).fetchone()
                show(r["path"])  # opens in your normal player or photo app
                out = {"opened": True}
            else:
                return self.send_json({"error": "not found"}, 404)
            self.send_json(out)
        except (ValueError, TypeError, OSError, sqlite3.Error, RuntimeError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:  # a bug: say so in the dashboard and record it in data/log.txt
            import traceback
            traceback.print_exc()
            self.send_json({"error": f"Something went wrong ({type(e).__name__}). Details are in data/log.txt."}, 500)


# ---------- self-check ----------

def selftest():
    global DATA, REVIEW, ROOTS
    tmp = Path(tempfile.mkdtemp(prefix="sorter-test-"))
    DATA, REVIEW, ROOTS = tmp / "data", tmp / "_Review", [tmp / "lib"]
    game = tmp / "lib" / "tournament"
    game.mkdir(parents=True)
    rng = np.random.default_rng(1)
    sharp = Image.fromarray(rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8))
    sharp.save(game / "sharp.png")
    shutil.copy(game / "sharp.png", game / "sharp copy.png")
    ramp = np.tile(np.linspace(20, 60, 640, dtype=np.uint8), (360, 1))
    Image.fromarray(np.dstack([ramp] * 3)).save(game / "blurry_dark.png")  # smooth, dim, small: no detail at all
    Image.new("RGB", (1920, 1080)).save(game / "pocket.png")
    CFG["min_photo_kb"] = 0
    scan()
    c = db()
    got = {os.path.basename(r["path"]): r for r in c.execute("SELECT * FROM media")}
    assert len(got) == 4 and not any(r["err"] for r in got.values()), [r["err"] for r in got.values()]
    assert got["sharp.png"]["score"] >= 4, got["sharp.png"]["score"]
    assert got["blurry_dark.png"]["score"] == 1, got["blurry_dark.png"]["score"]
    assert got["pocket.png"]["score"] == 0
    assert got["sharp copy.png"]["dup_of"] == got["sharp.png"]["id"]
    assert got["sharp.png"]["grp"], "shots taken together should share a group"
    assert listing({"q": "tournament"})["total"] == 4 and listing({"view": "flagged"})["total"] == 3
    update([got["blurry_dark.png"]["id"]], {"category": "B-roll"})  # B-roll is only flagged at score 0
    assert listing({"view": "flagged"})["total"] == 2
    undo()
    small = game.parent / "web"  # a resized copy in another folder is a near-duplicate of the original
    small.mkdir()
    scene = Image.fromarray(rng.integers(0, 255, (9, 16, 3), dtype=np.uint8)).resize((1920, 1080), Image.BICUBIC)
    scene.save(game / "scene.png")
    scene.resize((960, 540)).save(small / "scene_small.png")
    scan()
    full, web = (c.execute("SELECT id, dup_of FROM media WHERE path LIKE ?", (f"%{n}.png",)).fetchone()
                 for n in ("scene", "scene_small"))
    assert web["dup_of"] == full["id"] and full["dup_of"] is None, "resized copy should point at the original"
    os.remove(game / "scene.png")
    os.remove(small / "scene_small.png")
    scan()
    c.execute("DELETE FROM media WHERE status='missing'")
    c.commit()

    bad = got["pocket.png"]
    assert delete([bad["id"]]) == 0 and os.path.exists(bad["path"]), "must refuse to delete outside review"
    assert relocate([bad["id"]], True) == 1 and not os.path.exists(bad["path"])
    assert list((REVIEW / "rejects").iterdir()), "file should be in the review folder"
    assert undo() == "move to review" and os.path.exists(bad["path"])
    update([bad["id"]], {"user_score": 5, "category": "B-roll"}, add=["Keeper "])
    assert listing({"q": "keeper", "min": 5, "cat": "B-roll"})["total"] == 1
    undo()
    assert listing({"q": "keeper"})["total"] == 0 and listing({"cat": "B-roll"})["total"] == 0
    global EXPORTS
    EXPORTS = tmp / "exports"
    out = export([got["sharp.png"]["id"], bad["id"]], "My <reel>!")
    folder = Path(out["folder"])
    assert folder.name == "My reel" and out["files"] == 2 and len(list(folder.glob("00*.png"))) == 2
    assert os.path.exists(got["sharp.png"]["path"]), "export must copy, never move"
    (folder / "apply.json").write_text(json.dumps([{"id": bad["id"], "add_tags": ["from claude"], "score": 2},
                                                   {"id": got["sharp.png"]["id"], "category": "Action"}]))
    assert apply_edits(folder / "apply.json") == 2
    assert listing({"q": "from claude", "score": 2})["total"] == 1 and listing({"cat": "Action"})["total"] == 1
    assert undo() == "import from Claude" and listing({"q": "from claude"})["total"] == 0 and listing({"cat": "Action"})["total"] == 0
    scan()  # a second scan must not redo or duplicate anything
    assert c.execute("SELECT count(*) FROM media").fetchone()[0] == 4 and scan_state["total"] == 0
    c.close()
    _local.c = None
    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--apply", metavar="FILE")
    ap.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    ap.add_argument("--retry", action="store_true", help="wait for the port (used when restarting after an update)")
    args = ap.parse_args()
    if args.selftest:
        selftest()
    elif args.apply:
        print("applied edits to", apply_edits(args.apply), "files; Undo in the dashboard reverses them")
    elif args.rescore:
        print("rescored", rescore(), "files")
    elif args.scan:
        t = threading.Thread(target=scan, daemon=True)
        t.start()
        while t.is_alive():
            t.join(5)
            print(f"\r{scan_state['done']}/{scan_state['total']} {scan_state['current'][:50]:50}", end="", flush=True)
        print(f"\ndone, {scan_state['errors']} unreadable")
    else:
        url = f"http://localhost:{CFG['port']}"
        if sys.stderr is None:  # started without a console window (pythonw): problems go to data/log.txt
            DATA.mkdir(exist_ok=True)
            sys.stdout = sys.stderr = open(DATA / "log.txt", "a", encoding="utf-8", buffering=1)
        srv = None
        for _ in range(20 if args.retry else 1):  # after an update the old copy needs a moment to let go of the port
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", CFG["port"]), Handler)
                break
            except OSError:  # already running: just show it
                time.sleep(0.5)
        if args.open:
            import webbrowser
            webbrowser.open(url)
        if srv:
            threading.Thread(target=check, daemon=True).start()

            def newest():
                import update as updater
                update_state["latest"] = updater.latest()
            threading.Thread(target=newest, daemon=True).start()
            print(f"{time.strftime('%Y-%m-%d %H:%M')} Media Sorter running at {url}", flush=True)
            srv.serve_forever()

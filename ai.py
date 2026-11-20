"""Media Sorter, stage 2: the AI pass. Everything runs on this computer; nothing is uploaded.

Adds to each file: plain-language tags (hitting, team photo, beach...), a stored "look" so you can
search by describing a shot, faces grouped into people, closed-eye flags, and a dead-time map for videos.
Reads your files; writes only to data/ and models/ in this folder.

  python ai.py             process anything new, then refresh all AI tags
  python ai.py --selftest  run the built-in checks
  python ai.py --posted FOLDER NAME   mark library photos that also appear in FOLDER (e.g. an Instagram
                                      export) with the tags "posted" and "posted NAME"

Needs: onnxruntime, tokenizers, mediapipe (which brings OpenCV). Models download once into models/.
"""
import json, os, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps

import sorter as S

A = S.CFG["ai"]
MODELS = S.HERE / "models"
HF = "https://huggingface.co/"
URLS = {
    "clip_vision.onnx": HF + "Xenova/clip-vit-large-patch14/resolve/main/onnx/vision_model.onnx",
    "clip_text.onnx": HF + "Xenova/clip-vit-large-patch14/resolve/main/onnx/text_model.onnx",
    "clip_tokenizer.json": HF + "Xenova/clip-vit-large-patch14/resolve/main/tokenizer.json",
    "yunet.onnx": HF + "opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
    "sface.onnx": HF + "opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
                            "float16/latest/face_landmarker.task",
}
state = {"running": False, "done": 0, "total": 0, "current": "", "error": "", "skipped": 0}


def safe(fn, *a):
    """fn(*a), or None if it fails on this one file (the reason goes to data/log.txt)."""
    try:
        return fn(*a)
    except Exception as e:
        state["skipped"] += 1
        print(f"AI pass skipped a step on one file: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None


def fetch(name):
    p = MODELS / name
    if not p.exists():
        MODELS.mkdir(exist_ok=True)
        state["current"] = f"Downloading {name}"
        tmp = p.with_suffix(".part")
        urllib.request.urlretrieve(URLS[name], tmp)
        tmp.replace(p)
    return str(p)


def search_ready():
    """True when describing a shot in the search box can work without downloading anything."""
    return all((MODELS / n).exists() for n in ("clip_text.onnx", "clip_tokenizer.json"))


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


# ---------- the "look" of a picture, and words to compare it with (CLIP) ----------

@lru_cache(None)
def clip(part):
    import onnxruntime as ort
    return ort.InferenceSession(fetch(f"clip_{part}.onnx"), providers=["CPUExecutionProvider"])


@lru_cache(None)
def tokenizer():
    from tokenizers import Tokenizer
    return Tokenizer.from_file(fetch("clip_tokenizer.json"))


MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def pixels(im):
    im = im.convert("RGB")
    s = 224 / min(im.size)
    im = im.resize((max(224, round(im.width * s)), max(224, round(im.height * s))), Image.BICUBIC)
    l, t = (im.width - 224) // 2, (im.height - 224) // 2
    a = np.asarray(im.crop((l, t, l + 224, t + 224)), dtype=np.float32) / 255
    return ((a - MEAN) / STD).transpose(2, 0, 1)


def embed_images(ims):
    return unit(clip("vision").run(["image_embeds"], {"pixel_values": np.stack([pixels(i) for i in ims])})[0])


@lru_cache(512)
def embed_text(text):
    sess = clip("text")
    feed = {"input_ids": np.array([tokenizer().encode(text).ids[:77]], dtype=np.int64)}
    if any(i.name == "attention_mask" for i in sess.get_inputs()):
        feed["attention_mask"] = np.ones_like(feed["input_ids"])
    return unit(sess.run(["text_embeds"], feed)[0][0])


def looks(c, where="1", args=()):
    """(ids, matrix of stored looks) for the media rows matching a WHERE clause."""
    rows = c.execute(f"SELECT id, emb FROM media JOIN ai ON media_id=id WHERE emb IS NOT NULL AND {where}", args).fetchall()
    if not rows:
        return [], np.zeros((0, 768), dtype=np.float32)
    return [r["id"] for r in rows], np.stack([np.frombuffer(r["emb"], dtype=np.float16) for r in rows]).astype(np.float32)


def search(c, text, where, args):
    """Ids of the files that look most like the words, best first."""
    ids, E = looks(c, where, args)
    if not ids:
        return []
    sims = E @ embed_text("a photo of " + text.strip()[:200])
    return [ids[i] for i in np.argsort(-sims)[:A["search_top"]]]


def retag(c):
    """Rebuild every AI tag from stored results. Cheap, so wording in config.json can be tuned and re-run."""
    ids, E = looks(c)
    tags, cats = [], []
    for group in (A["tags"], A["places"]):
        if not ids:
            break
        T = np.stack([embed_text(g[1]) for g in group])
        z = 100 * E @ T.T
        P = np.exp(z - z.max(1, keepdims=True))
        P /= P.sum(1, keepdims=True)
        for mid, i, p in zip(ids, P.argmax(1), P.max(1)):
            if p >= A["tag_min"]:
                tags.append((mid, group[i][0], "ai"))
                if len(group[i]) > 2:
                    cats.append((group[i][2], mid))
    for r in c.execute("SELECT DISTINCT media_id, person FROM faces WHERE person IS NOT NULL"):
        tags.append((r["media_id"], r["person"], "face"))
    for r in c.execute("SELECT DISTINCT media_id FROM faces WHERE blink >= ?", (A["blink"],)):
        tags.append((r["media_id"], "eyes closed", "face"))
    for r in c.execute("SELECT media_id, dead FROM ai WHERE dead IS NOT NULL"):
        d = json.loads(r["dead"])
        if d["dark_frac"] > 0.6:
            tags.append((r["media_id"], "pocket video", "video"))
        elif d["dead_frac"] > 0.5:
            tags.append((r["media_id"], "mostly dead time", "video"))
    with S.WLOCK:
        c.execute("DELETE FROM tags WHERE src IN ('ai','face','video')")
        c.executemany("INSERT OR IGNORE INTO tags VALUES(?,?,?)", tags)
        # A category is only suggested where you have not set one; it never overwrites yours.
        c.executemany("UPDATE media SET category=? WHERE id=? AND category IS NULL", cats)
        c.commit()


def keypoints(im, size):
    import cv2
    im = im.convert("L")
    im.thumbnail((size, size))
    return cv2.ORB_create(1500).detectAndCompute(np.asarray(im), None)


def same_picture(a, b):
    """How many distinctive points line up between two pictures once one is shifted and scaled onto the other.
    Hundreds for a cropped or recompressed copy; a handful for a different photo of a similar scene."""
    import cv2
    (ka, da), (kb, db) = a, b
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return 0
    pairs = [p for p in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2) if len(p) == 2]
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return 0
    mask = cv2.findHomography(np.float32([ka[m.queryIdx].pt for m in good]),
                              np.float32([kb[m.trainIdx].pt for m in good]), cv2.RANSAC, 4.0)[1]
    return int(mask.sum()) if mask is not None else 0


def mark_posted(folder, label):
    """Tag library photos that also appear in `folder` (cropped, resized or recompressed copies still match).
    Returns (pictures checked, library photos tagged). Only tags are written; no file is touched."""
    from pathlib import Path
    label = "posted " + str(label).strip().lower()[:30]
    files = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in S.PHOTO)
    c = S.db()
    ids, E = looks(c, "kind='photo' AND status='active'")
    tkey = {r["id"]: r["tkey"] for r in c.execute("SELECT id, tkey FROM media")}
    hits = set()
    for n, p in enumerate(files):
        state.update(done=n, total=len(files), current="Comparing posts")
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        if not ids:
            break
        # Step 1: the 30 library photos that look most like the post. Step 2: keep only those that are
        # provably the same picture, so a different frame from the same burst is not marked.
        near = np.argsort(-(E @ embed_images([im])[0]))[:30]
        q = keypoints(im, 600)
        score = [same_picture(q, keypoints(Image.open(S.DATA / "thumbs" / (tkey[ids[j]] + ".jpg")), 480)) for j in near]
        bar = max(A["posted_points"], 0.6 * max(score))
        hits |= {ids[j] for j, s in zip(near, score) if s >= bar}  # duplicates of the same picture all get marked
    with S.WLOCK:
        c.execute("DELETE FROM tags WHERE tag=? AND src='posted'", (label,))
        c.executemany("INSERT OR IGNORE INTO tags VALUES(?,?,'posted')", [(m, label) for m in hits])
        c.execute("DELETE FROM tags WHERE tag='posted' AND src='posted'")
        c.execute("INSERT OR IGNORE INTO tags SELECT DISTINCT media_id, 'posted', 'posted' FROM tags WHERE src='posted'")
        c.commit()
    return len(files), len(hits)


# ---------- faces: find them, check the eyes, group them into people ----------

@lru_cache(None)
def yunet():
    import cv2
    return cv2.FaceDetectorYN.create(fetch("yunet.onnx"), "", (320, 320), A["face_min"], 0.3, 200)


@lru_cache(None)
def sface():
    import cv2
    return cv2.FaceRecognizerSF.create(fetch("sface.onnx"), "")


@lru_cache(None)
def landmarker():
    from mediapipe.tasks.python import BaseOptions, vision
    return vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=fetch("face_landmarker.task")),
        output_face_blendshapes=True, num_faces=1))


def load(path):
    """The photo at up to 2000 px, upright. None if it cannot be read."""
    try:
        im = Image.open(path)
        im.draft("RGB", (2400, 2400))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        im = S.grab(path)
        im = im and im.convert("RGB")
    if im:
        im.thumbnail((2000, 2000))
    return im


def eyes(crop):
    """0 (wide open) to 1 (shut) for the more open of the two eyes, or None if no face is found in the crop."""
    import mediapipe as mp
    crop = crop.resize((256, max(1, round(256 * crop.height / crop.width))))
    r = landmarker().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(np.asarray(crop))))
    if not r.face_blendshapes:
        return None
    b = {x.category_name: x.score for x in r.face_blendshapes[0]}
    return float(min(b["eyeBlinkLeft"], b["eyeBlinkRight"]))


def find_faces(im):
    """[(x, y, w, h as fractions of the picture, identity bytes, blink)] for each clear face."""
    bgr = np.ascontiguousarray(np.asarray(im)[:, :, ::-1])
    det = yunet()
    det.setInputSize((im.width, im.height))
    found = det.detect(bgr)[1]
    out = []
    for f in found if found is not None else []:
        x, y, w, h = (float(v) for v in f[:4])
        if w < A["face_px"]:
            continue  # too small to recognise anyone reliably
        emb = unit(sface().feature(sface().alignCrop(bgr, f))[0]).astype(np.float16).tobytes()
        m = 0.3 * w
        blink = eyes(im.crop((int(x - m), int(y - m), int(x + w + m), int(y + h + m)))) if w >= A["blink_px"] else None
        out.append((x / im.width, y / im.height, w / im.width, h / im.height, emb, blink))
    return out


def cluster(c):
    """Group unassigned faces into people. People you have already named keep their faces and gain new ones."""
    # Only faces big enough to tell people apart take part; small ones made groups by pose, not by person.
    rows = c.execute("SELECT id, media_id, emb, person FROM faces WHERE w >= ? OR person IS NOT NULL ORDER BY w DESC",
                     (A["person_px"],)).fetchall()
    if not rows:
        return
    E = np.stack([np.frombuffer(r["emb"], dtype=np.float16) for r in rows]).astype(np.float32)
    names, members = [], []
    C = np.zeros((len(rows) + 1, E.shape[1]), dtype=np.float32)  # running sum of each person's faces
    for i, r in enumerate(rows):
        if r["person"]:
            if r["person"] not in names:
                names.append(r["person"])
                members.append([])
            C[names.index(r["person"])] += E[i]
    # ponytail: greedy one-pass grouping, biggest faces first. Good for a few thousand faces;
    # swap for proper clustering (e.g. HDBSCAN) if people get split or merged too often.
    for i, r in enumerate(rows):
        if r["person"]:
            continue
        k = len(names)
        sims = unit(C[:k]) @ E[i] if k else np.zeros(1)
        j = int(sims.argmax()) if k else -1
        if j < 0 or sims[j] < A["same_person"]:
            names.append(None)
            members.append([])
            j = k
        C[j] += E[i]
        members[j].append(r)
    used = [int(n.split()[1]) for n in names if n and n.startswith("person ") and n.split()[1].isdigit()]
    n = max(used, default=0)
    with S.WLOCK:
        for name, faces in zip(names, members):
            if name is None:
                if len({f["media_id"] for f in faces}) < A["person_min_photos"]:
                    continue  # seen too rarely to be worth a name; retried on the next run
                n += 1
                name = f"person {n}"
            c.executemany("UPDATE faces SET person=? WHERE id=?", [(name, f["id"]) for f in faces])
        c.commit()


def rename_person(old, new):
    """Give a person a real name. Renaming to an existing name merges the two."""
    new = str(new).strip().lower()[:40]
    c = S.db()
    with S.WLOCK:
        if not new or not c.execute("SELECT 1 FROM faces WHERE person=?", (old,)).fetchone():
            return 0
        n = c.execute("UPDATE faces SET person=? WHERE person=?", (new, old)).rowcount
        c.execute("UPDATE OR IGNORE tags SET tag=? WHERE tag=? AND src='face'", (new, old))
        c.execute("DELETE FROM tags WHERE tag=? AND src='face'", (old,))
        c.commit()
    return n


# ---------- videos: where nothing is happening ----------

def runs(quiet, min_len):
    """[start, end) index pairs of every stretch of True at least min_len long."""
    out, start = [], None
    for i, q in enumerate([*quiet, False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if i - start >= min_len:
                out.append([start, i])
            start = None
    return out


def dead_time(path):
    """Stretches of a video with almost no movement, or too dark to see (lens covered, in a pocket)."""
    # ponytail: movement is judged on tiny frames, 2 per second. It finds still and dark stretches;
    # it cannot tell a rally from players walking around. That needs a sport-specific model.
    raw = S.ffrun(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "fps=2,scale=64:36", "-pix_fmt", "gray",
                   "-f", "rawvideo", "-"], timeout=3600)
    f = np.frombuffer(raw[:len(raw) // 2304 * 2304], dtype=np.uint8).reshape(-1, 36, 64).astype(np.int16)
    if len(f) < 4:
        return None
    motion = np.abs(np.diff(f, axis=0)).mean((1, 2))
    dark = f[1:].mean((1, 2)) < S.CFG["scoring"]["dead_mean"]
    quiet = (motion < max(A["still"], A["dead_ratio"] * float(np.median(motion)))) | dark
    segs = [[s / 2, e / 2] for s, e in runs(quiet, A["dead_seconds"] * 2)]
    return {"dead": segs, "dead_frac": round(sum(e - s for s, e in segs) * 2 / len(quiet), 3),
            "dark_frac": round(float(dark.mean()), 3)}


# ---------- the pass itself ----------

def run():
    if state["running"]:
        return
    state.update(running=True, done=0, total=0, current="Loading models", error="", skipped=0)
    try:
        c = S.db()
        todo = c.execute("SELECT id, path, kind, tkey FROM media LEFT JOIN ai ON media_id=id "
                         "WHERE status='active' AND err IS NULL AND media_id IS NULL ORDER BY path").fetchall()
        state["total"] = len(todo)
        with ThreadPoolExecutor(S.CFG["workers"]) as ex:
            for i in range(0, len(todo), 8):
                batch = todo[i:i + 8]
                thumbs = [safe(Image.open, S.DATA / "thumbs" / (r["tkey"] + ".jpg")) for r in batch]
                ok = [i for i, t in enumerate(thumbs) if t is not None]
                E = [None] * len(batch)
                for i, e in zip(ok, embed_images([thumbs[i] for i in ok]) if ok else []):
                    E[i] = e
                big = list(ex.map(lambda r: safe(load, r["path"]) if r["kind"] == "photo" else None, batch))
                faces = [(safe(find_faces, im) or []) if im is not None else [] for im in big]
                dead = [safe(dead_time, r["path"]) if r["kind"] == "video" else None for r in batch]
                with S.WLOCK:
                    for r, e, fs, d in zip(batch, E, faces, dead):
                        c.execute("DELETE FROM faces WHERE media_id=?", (r["id"],))
                        c.executemany("INSERT INTO faces(media_id,x,y,w,h,emb,blink) VALUES(?,?,?,?,?,?,?)",
                                      [(r["id"], *f) for f in fs])
                        c.execute("INSERT OR REPLACE INTO ai VALUES(?,?,?)",
                                  (r["id"], None if e is None else e.astype(np.float16).tobytes(), json.dumps(d) if d else None))
                    c.commit()
                state["done"] += len(batch)
                state["current"] = os.path.basename(batch[-1]["path"])
        state["current"] = "Grouping faces and tagging"
        cluster(c)
        retag(c)
    except Exception as e:  # shown in the dashboard instead of silently stopping
        import traceback
        traceback.print_exc()
        state["error"] = f"{type(e).__name__}: {e}"
    finally:
        state.update(running=False, current="")


def selftest():
    assert runs([1, 1, 0, 1, 1, 1, 0, 1], 2) == [[0, 2], [3, 6]]
    import tempfile, shutil
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp(prefix="sorter-ai-test-"))
    S.DATA = tmp / "data"
    c = S.db()
    rng = np.random.default_rng(2)
    who = unit(rng.normal(size=(2, 128)))
    for i in range(8):  # two people, four photos each, plus one stranger seen once
        e = unit(who[i % 2] + 0.05 * rng.normal(size=128)).astype(np.float16).tobytes()
        c.execute("INSERT INTO faces(media_id,x,y,w,h,emb,blink) VALUES(?,0,0,?,0,?,?)", (i, 0.2 - i / 100, e, 0.9 if i == 0 else 0.1))
    c.execute("INSERT INTO faces(media_id,x,y,w,h,emb) VALUES(99,0,0,0.1,0,?)", (unit(rng.normal(size=128)).astype(np.float16).tobytes(),))
    cluster(c)
    got = dict(c.execute("SELECT person, count(*) FROM faces GROUP BY 1").fetchall())
    assert got == {"person 1": 4, "person 2": 4, None: 1}, got
    assert rename_person("person 2", " Sam ") == 4 and rename_person("nobody", "x") == 0
    retag(c)
    assert c.execute("SELECT count(*) FROM tags WHERE tag='sam' AND src='face'").fetchone()[0] == 4
    assert [r[0] for r in c.execute("SELECT media_id FROM tags WHERE tag='eyes closed'")] == [0]
    c.close()
    S._local.c = None
    shutil.rmtree(tmp, ignore_errors=True)
    print("ai selftest passed")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--posted" in sys.argv:
        folder, name = sys.argv[sys.argv.index("--posted") + 1:][:2]
        print("checked %d pictures, tagged %d library photos" % mark_posted(folder, name))
    else:
        import threading
        t = threading.Thread(target=run, daemon=True)
        t.start()
        while t.is_alive():
            t.join(5)
            print(f"\r{state['done']}/{state['total']} {state['current'][:50]:50}", end="", flush=True)
        print("\ndone")

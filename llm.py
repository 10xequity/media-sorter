"""Media Sorter: optional link to a large AI model that looks at a photo and writes a caption and tags.

Off until you choose a service in the dashboard ("AI services"). Four choices:
  ollama   a model running on this computer (free, private, slower). Needs Ollama installed.
  claude   Anthropic's Claude    } these three send the photo over the internet to that company
  openai   OpenAI's ChatGPT API  } and bill the account that owns the key, per photo.
  gemini   Google's Gemini       }
The cloud services need an API key (a pay-per-use password from that company's developer site). None of them
lets an outside app use a personal subscription login. The key is kept in config.json on this computer only.
"""
import base64, io, json, re, subprocess, tempfile
from pathlib import Path

import sorter as S

PROMPT = ('Look at this photo from a volleyball club\'s library. Reply with JSON only, in this exact shape: '
          '{"caption": "one plain sentence saying what is happening", "tags": ["up to 8 short lowercase tags"]}. '
          'Tags should be things someone would search for: the action, the setting, objects such as shoes or a trophy, '
          'the mood. Do not guess names, ages or identities of people.')
MODELS = {"ollama": "lfm3-vl:2b", "claude": "claude-haiku-5-5", "openai": "gpt-6-luna", "gemini": "gemini-4-hydrogen"}
NAMES = {"ollama": "the model on this computer", "claude": "Anthropic (Claude)", "openai": "OpenAI", "gemini": "Google (Gemini)"}
OLLAMA = "http://localhost:11434"
state = {"running": False, "done": 0, "total": 0, "error": ""}


def settings():
    s = S.CFG.get("llm") or {}
    p = s.get("provider") if s.get("provider") in MODELS else ""
    return {"provider": p, "model": s.get("model") or MODELS.get(p, ""), "key": s.get("key", "")}


def post(url, body, headers=(), timeout=180):
    """POST JSON with curl and return the parsed reply. The key travels in a private temp file, never on the command line."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "h").write_text("\n".join(["Content-Type: application/json", *headers]), encoding="utf-8")
        (Path(d) / "b").write_text(json.dumps(body), encoding="utf-8")
        r = subprocess.run(["curl", "-sS", "--max-time", str(timeout), "-X", "POST", url, "-H", "@" + str(Path(d) / "h"),
                            "--data-binary", "@" + str(Path(d) / "b")], capture_output=True, timeout=timeout + 10,
                           creationflags=S.NOWIN)
    if r.returncode:
        raise RuntimeError("Could not reach the AI service: " + r.stderr.decode(errors="replace").strip()[:200])
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("The AI service sent an unreadable reply.")
    err = out.get("error") if isinstance(out, dict) else None
    if err:
        raise RuntimeError("The AI service refused: " + str(err.get("message") if isinstance(err, dict) else err)[:300])
    return out


def ask(provider, model, key, jpeg):
    """Send one JPEG and the prompt; return the model's text."""
    b64 = base64.b64encode(jpeg).decode()
    if provider == "ollama":
        return post(OLLAMA + "/api/generate", {"model": model, "prompt": PROMPT, "images": [b64], "stream": False}, timeout=600)["response"]
    if provider == "claude":
        out = post("https://api.anthropic.com/v1/messages",
                   {"model": model, "max_tokens": 400, "messages": [{"role": "user", "content": [
                       {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                       {"type": "text", "text": PROMPT}]}]},
                   ["x-api-key: " + key, "anthropic-version: 2026-10-21"])
        return "".join(b.get("text", "") for b in out["content"] if b.get("type") == "text")
    if provider == "openai":
        out = post("https://api.openai.com/v1/responses",
                   {"model": model, "input": [{"role": "user", "content": [
                       {"type": "input_text", "text": PROMPT},
                       {"type": "input_image", "image_url": "data:image/jpeg;base64," + b64}]}]},
                   ["Authorization: Bearer " + key])
        return "".join(c.get("text", "") for o in out["output"] if o.get("type") == "message"
                       for c in o.get("content", []) if c.get("type") == "output_text")
    if provider == "gemini":
        out = post("https://generativelanguage.googleapis.com/v1beta/interactions",
                   {"model": model, "input": [{"type": "text", "text": PROMPT},
                                              {"type": "image", "data": b64, "mime_type": "image/jpeg"}]},
                   ["x-goog-api-key: " + key])
        return "".join(c.get("text", "") for s in out["steps"] if s.get("type") == "model_output" for c in s.get("content", []))
    raise RuntimeError("Choose an AI service first.")


def parse(text):
    """(caption, tags) from the model's reply, tolerating extra words around the JSON."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        j = json.loads(m.group(0)) if m else {}
    except ValueError:
        j = {}
    tags = [re.sub(r"[^\w '&-]", "", str(t)).strip().lower()[:30] for t in j.get("tags", []) if isinstance(t, (str, int))][:8]
    return str(j.get("caption", "")).strip()[:300], [t for t in tags if t]


def picture(row):
    """The photo as a JPEG no longer than 1568 px on its long edge (larger adds cost, not detail)."""
    src = S.screen(row)
    if not src:
        return None
    from PIL import Image
    im = Image.open(src).convert("RGB")
    im.thumbnail((1568, 1568))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def describe(ids):
    """Caption and tag the chosen photos. Runs in the background; progress is in state."""
    if state["running"]:
        return
    s = settings()
    state.update(running=True, done=0, total=0, error="")
    try:
        if not s["provider"] or (s["provider"] != "ollama" and not s["key"]):
            raise RuntimeError("Choose an AI service and enter its key first (AI services).")
        c = S.db()
        rows = c.execute(f"SELECT id, path, tkey, kind FROM media WHERE kind='photo' AND err IS NULL AND id {S.IN}",
                         (json.dumps(ids[:500]),)).fetchall()
        state["total"] = len(rows)
        for r in rows:
            jpeg = picture(r)
            if jpeg:
                caption, tags = parse(ask(s["provider"], s["model"], s["key"], jpeg))
                with S.WLOCK:
                    c.execute("DELETE FROM tags WHERE media_id=? AND src='llm'", (r["id"],))
                    c.executemany("INSERT OR IGNORE INTO tags VALUES(?,?,'llm')", [(r["id"], t) for t in tags])
                    c.execute("INSERT OR REPLACE INTO captions VALUES(?,?,?)", (r["id"], caption, s["provider"]))
                    c.commit()
            state["done"] += 1
    except Exception as e:  # stop at the first failure: a wrong key would otherwise fail (and bill) once per photo
        state["error"] = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
    finally:
        state["running"] = False


def status():
    s = settings()
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "2", OLLAMA + "/api/tags"], capture_output=True, timeout=5, creationflags=S.NOWIN)
        local = [m["name"] for m in json.loads(r.stdout).get("models", [])] if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        local = None
    return {"provider": s["provider"], "model": s["model"], "has_key": bool(s["key"]), "defaults": MODELS, "names": NAMES,
            "ollama": local}  # None: Ollama is not running; a list: the models it has


def save(provider, model, key):
    """Keep the choice in config.json on this computer. An empty key keeps the one already saved."""
    if provider not in ("", *MODELS):
        raise ValueError("Unknown AI service.")
    old = S.CFG.get("llm") or {}
    S.CFG["llm"] = {"provider": provider, "model": str(model).strip()[:80] or MODELS.get(provider, ""),
                    "key": (str(key).strip() or old.get("key", "")) if provider == old.get("provider") or key else str(key).strip()}
    S.save_cfg()
    return status()


def selftest():
    assert parse('Sure! {"caption": "A player spikes.", "tags": ["Hitting", "grass ", 3, {"x": 1}]} done') == ("A player spikes.", ["hitting", "grass", "3"])
    assert parse("no json here") == ("", [])
    # The request and reply shapes, checked against a stand-in server (the real services need a key and cost money).
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = {}

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.update(path=self.path, images=body.get("images"), ctype=self.headers["Content-Type"])
            out = json.dumps({"response": '{"caption": "ok", "tags": ["test"]}'}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    global OLLAMA
    OLLAMA = f"http://127.0.0.1:{srv.server_port}"
    assert parse(ask("ollama", "m", "", b"jpegbytes")) == ("ok", ["test"])
    assert seen == {"path": "/api/generate", "images": [base64.b64encode(b"jpegbytes").decode()], "ctype": "application/json"}
    srv.shutdown()
    print("llm selftest passed")


if __name__ == "__main__":
    selftest()

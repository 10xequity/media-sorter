"""Media Sorter updater. Replaces the program files with a newer version; never touches this computer's
settings (config.json), library (data/), AI models, exports or installed Python.

  python update.py            download the newest version from GitHub and install it
  python update.py FILE.zip   install from a zip you were sent (no internet needed)
  python update.py --check    print the installed and newest version numbers

The dashboard's Update button calls apply(). The files replaced are backed up in data/backup/ first.
Stands alone on purpose: it must not import sorter.py, which it replaces.
"""
import io, json, shutil, subprocess, sys, time, urllib.request, zipfile
from pathlib import Path

HERE = Path(__file__).parent
REPO = "10xequity/media-sorter"
KEEP = {"config.json", "data", "models", "exports", "dist", "bin", ".venv", ".git", "_Review"}  # never overwritten


def installed():
    f = HERE / "VERSION"
    return f.read_text().strip() if f.exists() else "dev"


def latest():
    """The newest published version number, or None when offline."""
    try:
        with urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/main/VERSION", timeout=6) as r:
            return r.read(40).decode().strip()
    except OSError:
        return None


def newer(a, b):
    """True if version a is newer than b ("0.10.0" beats "0.9.2")."""
    num = lambda v: [int(x) for x in v.split(".") if x.isdigit()]
    return num(a) > num(b)


def members(z):
    """(name inside the app folder, zip entry) for every program file. GitHub zips wrap everything in one folder."""
    names = [i.filename for i in z.infolist() if not i.is_dir()]
    top = names[0].split("/")[0] + "/" if names and all(n.startswith(names[0].split("/")[0] + "/") for n in names) else ""
    out = []
    for i in z.infolist():
        rel = i.filename[len(top):]
        if i.is_dir() or not rel:
            continue
        dest = (HERE / rel).resolve()
        if not dest.is_relative_to(HERE.resolve()):
            raise RuntimeError(f"Refusing this update: it tries to write outside the app folder ({i.filename})")
        if rel.split("/")[0] not in KEEP:
            out.append((rel, i))
    return out


def apply(source=None):
    """Install an update from a zip file, or from GitHub when no file is given."""
    if source:
        data = Path(source).read_bytes()
    else:
        with urllib.request.urlopen(f"https://github.com/{REPO}/archive/refs/heads/main.zip", timeout=60) as r:
            data = r.read()
    z = zipfile.ZipFile(io.BytesIO(data))
    files = members(z)
    if "sorter.py" not in {rel for rel, _ in files}:
        raise RuntimeError("That zip is not a Media Sorter update.")
    new = next((z.read(i).decode().strip() for rel, i in files if rel == "VERSION"), "unknown")
    old, old_reqs = installed(), (HERE / "requirements.txt").read_bytes() if (HERE / "requirements.txt").exists() else b""
    backup = HERE / "data" / "backup" / f"{old} {time.strftime('%Y-%m-%d %H%M%S')}"
    for rel, i in files:
        dest = HERE / rel
        if dest.exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup / rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = z.read(i)
        if rel.endswith(".command"):
            data = data.replace(b"\r\n", b"\n")
        dest.write_bytes(data)
        if rel.endswith(".command"):
            dest.chmod(0o755)
    if (HERE / "requirements.txt").read_bytes() != old_reqs:  # the new version needs different add-ons
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(HERE / "requirements.txt")], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    return {"updated": True, "from": old, "to": new, "files": len(files), "backup": str(backup)}


def selftest():
    assert newer("0.10.0", "0.9.2") and not newer("0.6.0", "0.6.0") and newer("1.0", "dev")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("media-sorter-main/sorter.py", "x")
        z.writestr("media-sorter-main/config.json", "{}")
        z.writestr("media-sorter-main/data/library.db", "x")
    assert [rel for rel, _ in members(zipfile.ZipFile(buf))] == ["sorter.py"], "settings and library must be left alone"
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.py", "x")
        z.writestr("sorter.py", "x")
    try:
        members(zipfile.ZipFile(bad))
        raise AssertionError("a zip that writes outside the app folder must be refused")
    except RuntimeError:
        pass
    print("update selftest passed")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--check" in sys.argv:
        print(json.dumps({"installed": installed(), "latest": latest()}))
    else:
        print(json.dumps(apply(sys.argv[1] if len(sys.argv) > 1 else None)))

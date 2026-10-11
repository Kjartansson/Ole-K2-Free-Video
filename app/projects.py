"""Project folders: projects/<slug>/ holds script.txt, project.json, work/ (generated parts),
final.mp4, cover.jpg, caption.txt, job.json (render progress) and render.log."""
import datetime
import json
import os
import random
import re
import shutil

import config
import scriptfmt


def path(slug, *parts):
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,60}", slug or ""):
        raise ValueError("bad project name")
    return os.path.join(config.PROJECTS_DIR, slug, *parts)


def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:40] or "video"
    base, n = s, 2
    while os.path.exists(os.path.join(config.PROJECTS_DIR, s)):
        s = f"{base}-{n}"
        n += 1
    return s


def create(text, name="", output_name=""):
    sc = scriptfmt.parse(text)
    name = (name or sc["title"] or "My video").strip()[:80]
    slug = slugify(name)
    os.makedirs(path(slug, "work"), exist_ok=True)
    save_meta(slug, {"name": name, "title": sc["title"], "created": _now(), "seed": random.randint(1, 10**6),
                     "output_name": safe_filename(output_name), "options": {}, "scenes": {}, "posted": "",
                     "exports": []})
    save_script(slug, text)
    return slug


def safe_filename(name):
    """User's output file name -> safe for every OS ('' stays '')."""
    n = os.path.splitext((name or "").strip())[0] if (name or "").lower().endswith((".mp4", ".mov")) else (name or "").strip()
    n = re.sub(r"[^\w\-. ]+", "", n, flags=re.UNICODE).strip(" .")
    return re.sub(r"\s+", "-", n)[:80]


def export_name(output_name, slug, stamp):
    """Export base name: the user's name, or date_project; never one that exists already."""
    base = safe_filename(output_name) or f"{stamp:%Y-%m-%d_%H%M}_{slug}"
    name, n = base, 2
    while os.path.exists(os.path.join(config.EXPORTS_DIR, name + ".mp4")):
        name = f"{base}-{n}"
        n += 1
    return name


def rename(slug, name=None, output_name=None):
    """Change the project name (folder follows) and/or the output file name. Returns the slug."""
    m = load_meta(slug)
    if output_name is not None:
        m["output_name"] = safe_filename(output_name)
    if name is not None and name.strip() and name.strip() != m.get("name"):
        m["name"] = name.strip()[:80]
        new = slugify(m["name"])
        if new != slug and job(slug).get("state") != "running":
            shutil.move(path(slug), os.path.join(config.PROJECTS_DIR, new))
            slug = new
    save_meta(slug, m)
    return slug


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_meta(slug):
    try:
        with open(path(slug, "project.json")) as f:
            m = json.load(f)
    except (OSError, ValueError):
        m = {}
    m.setdefault("options", {})
    m.setdefault("scenes", {})
    m.setdefault("exports", [])
    m.setdefault("seed", 1)
    return m


def save_meta(slug, meta):
    p = path(slug, "project.json")
    with open(p + ".tmp", "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(p + ".tmp", p)


def load_script(slug):
    try:
        with open(path(slug, "script.txt"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def save_script(slug, text):
    with open(path(slug, "script.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    m = load_meta(slug) if os.path.exists(path(slug, "project.json")) else None
    if m is not None:
        t = scriptfmt.parse(text)["title"]
        if t:
            m["title"] = t
            m.setdefault("name", t)
            save_meta(slug, m)


def job(slug):
    try:
        with open(path(slug, "job.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"state": "idle"}


def summary(slug):
    m = load_meta(slug)
    final = path(slug, "final.mp4")
    has = os.path.exists(final)
    cap = ""
    try:
        with open(path(slug, "caption.txt"), encoding="utf-8") as f:
            cap = f.read()
    except OSError:
        pass
    return {"slug": slug, "name": m.get("name") or m.get("title") or slug, "title": m.get("title", ""),
            "output_name": m.get("output_name", ""), "created": m.get("created", ""),
            "final": f"projects/{slug}/final.mp4" if has else "",
            "cover": f"projects/{slug}/cover.jpg" if os.path.exists(path(slug, "cover.jpg")) else "",
            "rendered": m.get("rendered", ""), "duration": m.get("duration", 0), "caption": cap,
            "posted": m.get("posted", ""), "exports": m.get("exports", []), "job": job(slug).get("state", "idle")}


def all_projects():
    out = []
    for d in os.listdir(config.PROJECTS_DIR):
        if d.startswith(".") or not os.path.isfile(os.path.join(config.PROJECTS_DIR, d, "script.txt")):
            continue
        try:
            out.append(summary(d))
        except ValueError:
            continue
    return sorted(out, key=lambda p: p.get("rendered") or p.get("created") or "", reverse=True)


def _trash_exports(names, stamp):
    """Move exported files (mp4 + jpg + txt) to exports/.trash/<stamp>/."""
    moved = []
    dest = os.path.join(config.EXPORTS_DIR, ".trash", stamp)
    for name in names or []:
        base = os.path.splitext(os.path.basename(name))[0]
        for ext in (".mp4", ".jpg", ".txt"):
            src = os.path.join(config.EXPORTS_DIR, base + ext)
            if os.path.isfile(src):
                os.makedirs(dest, exist_ok=True)
                shutil.move(src, os.path.join(dest, base + ext))
                moved.append(base + ext)
    return moved


def trash_video(slug):
    """Remove the finished video (final.mp4, cover, caption + its copies in exports/) by moving them to
    .trash folders. The project and its generated parts stay, so rendering again is quick."""
    stamp = f"{datetime.datetime.now():%Y%m%d-%H%M%S}"
    m = load_meta(slug)
    moved = _trash_exports(m.get("exports"), stamp)
    tdir = path(slug, ".trash", stamp)
    for name in ("final.mp4", "cover.jpg", "caption.txt"):
        if os.path.isfile(path(slug, name)):
            os.makedirs(tdir, exist_ok=True)
            shutil.move(path(slug, name), os.path.join(tdir, name))
            moved.append(name)
    for k in ("rendered", "duration", "posted", "last_quality", "shots"):
        m.pop(k, None)
    m["exports"] = []
    save_meta(slug, m)
    return moved


def trash(slug):
    """Move the project and its exported videos to .trash folders (nothing is deleted for good)."""
    stamp = f"{datetime.datetime.now():%Y%m%d-%H%M%S}"
    _trash_exports(load_meta(slug).get("exports"), stamp)
    t = os.path.join(config.PROJECTS_DIR, ".trash")
    os.makedirs(t, exist_ok=True)
    shutil.move(path(slug), os.path.join(t, f"{slug}-{stamp}"))


def scene_files(slug, n):
    """Thumbnails/clips that exist for the storyboard cards."""
    out = []
    for i in range(1, n + 1):
        f = {}
        for key, name in (("image", f"s{i:02d}_img.png"), ("clip", f"s{i:02d}_clip.mp4"),
                          ("voice", f"s{i:02d}_voice.wav"), ("preview", f"s{i:02d}_preview.mp4")):
            p = path(slug, "work", name)
            if os.path.exists(p):
                f[key] = f"projects/{slug}/work/{name}?t={int(os.path.getmtime(p))}"
        out.append(f)
    return out

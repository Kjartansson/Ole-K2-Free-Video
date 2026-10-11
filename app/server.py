"""Local web GUI server (stdlib only). Start with factory.sh / factory.bat, or:
    python3 app/server.py [--port 8770] [--host 127.0.0.1]
"""
import argparse
import importlib
import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy      # noqa: E402
import config     # noqa: E402
import errors     # noqa: E402
import llm        # noqa: E402
import media      # noqa: E402
import models     # noqa: E402
import pipeline   # noqa: E402
import projects   # noqa: E402
import scriptfmt  # noqa: E402
from errors import FactoryError  # noqa: E402

FILE_ROOTS = {"projects": config.PROJECTS_DIR, "exports": config.EXPORTS_DIR, "music": config.MUSIC_DIR,
              "sfx": config.SFX_DIR, "voices": config.VOICES_DIR,
              "templates": os.path.join(config.ROOT, "templates"), "examples": os.path.join(config.ROOT, "examples")}
MAX_UPLOAD = 2 * 1024**3
RENDER = {"slug": None, "proc": None}
TASKS = {}
LOCK = threading.Lock()


_MODS = None
_MTIME = 0.0


def maybe_reload():
    """Pick up updated app/*.py without restarting (the GUI keeps running while the factory is improved)."""
    global _MODS, _MTIME
    if _MODS is None:
        _MODS = [config, errors, scriptfmt, comfy, llm, media, projects, models, pipeline]
    app = os.path.join(config.ROOT, "app")
    newest = max(os.path.getmtime(os.path.join(app, f)) for f in os.listdir(app)
                 if f.endswith(".py") and f != "server.py")
    if not _MTIME:
        _MTIME = newest
    elif newest > _MTIME:
        _MTIME = newest
        for m in _MODS:
            importlib.reload(m)
        apply_settings()


def apply_settings(s=None):
    s = s or config.load_settings()
    comfy.configure(s["comfy_url"])
    llm.configure(s["ollama_url"])
    return s


# ------------------------------------------------------------------ background tasks
def start_task(kind, fn, label=""):
    tid = uuid.uuid4().hex[:10]
    t = {"id": tid, "kind": kind, "label": label, "state": "running", "stage": "", "text": "", "done": 0,
         "total": 0, "speed": 0, "result": None, "error": "", "cancel": False, "started": time.time()}
    TASKS[tid] = t

    def runner():
        try:
            t["result"] = fn(t)
            t["state"] = "done"
        except Exception as e:
            if t["cancel"] or errors.classify(e) == "CANCELLED":
                t["state"], t["error"] = "cancelled", "Stopped"
            else:
                entry = errors.record(e, f"task:{kind}", task=label)
                t.update(state="error", error=str(e)[:600], code=entry["code"], hint=entry["hint"], ref=entry["ref"],
                         detail_file=entry["ctx"].get("detail_file", ""))
        t["finished"] = time.time()
    threading.Thread(target=runner, daemon=True).start()
    return tid


def task_view(t):
    return {k: v for k, v in t.items() if k != "cancel"}


def pid_alive(pid):
    """Is this render process still running? (renders outlive a restarted GUI or come from the CLI)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.path.exists("/proc"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read()
            return b"pipeline" in cmd or b"factory.py" in cmd
        except OSError:
            return False
    if platform.system() == "Windows":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_render():
    """Slug of any render running right now, started by this GUI, another one, or the command line."""
    p = RENDER["proc"]
    if p is not None and p.poll() is None:
        return RENDER["slug"]
    for d in os.listdir(config.PROJECTS_DIR):
        if d.startswith("."):
            continue
        try:
            j = projects.job(d)
        except ValueError:
            continue
        if j.get("state") == "running" and pid_alive(j.get("pid")):
            return d
    return None


def gpu_busy():
    return running_render() is not None


def busy_tasks():
    return [task_view(t) for t in TASKS.values() if t["state"] == "running"]


# ------------------------------------------------------------------ render process
def start_render(slug, scene=None):
    with LOCK:
        busy = running_render()
        if busy:
            raise FactoryError("BUSY", f"Already rendering '{busy}'. Wait for it or cancel it.")
        if any(t["kind"] in ("llm", "voice") and t["state"] == "running" for t in TASKS.values()):
            raise FactoryError("BUSY", "The local AI is still writing. Wait until it has finished.")
        args = [sys.executable, os.path.join(config.ROOT, "app", "pipeline.py"), "render", slug]
        if scene:
            args += ["--scene", str(int(scene))]
        # fresh state at once, so nobody reads the previous run's "done" while the process starts
        with open(projects.path(slug, "job.json"), "w") as f:
            json.dump({"state": "running", "stage": "starting", "pct": 0, "message": "Starting",
                       "started": time.time(), "updated": time.time(), "warnings": []}, f)
        out = open(os.path.join(config.LOGS_DIR, "render-stdout.log"), "a")
        kw = {"creationflags": 0x00000200} if platform.system() == "Windows" else {"start_new_session": True}
        RENDER.update(slug=slug, proc=subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, cwd=config.ROOT, **kw))


def cancel_render(slug):
    p = RENDER["proc"]
    if p is not None and p.poll() is None and RENDER["slug"] == slug:
        p.terminate()
        comfy.interrupt()
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()
    else:
        j = projects.job(slug)                 # a render this GUI did not start (restarted GUI, CLI)
        if j.get("state") == "running" and pid_alive(j.get("pid")):
            try:
                os.kill(int(j["pid"]), 15)
            except OSError as e:
                errors.quiet("server", e, "cancel foreign render")
            comfy.interrupt()
    j = projects.job(slug)
    if j.get("state") == "running":
        j.update(state="cancelled", message="Cancelled")
        with open(projects.path(slug, "job.json"), "w") as f:
            json.dump(j, f)


def job_of(slug):
    j = projects.job(slug)
    if j.get("state") == "running":
        mine = RENDER["slug"] == slug and RENDER["proc"] is not None and RENDER["proc"].poll() is None
        if not mine and not pid_alive(j.get("pid")) and time.time() - j.get("updated", 0) > 30:
            j.update(state="stopped", message="The render stopped (the program was closed?). Render again: "
                                              "finished parts are reused.")
    try:
        with open(projects.path(slug, "render.log"), encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 3000))
            j["log"] = "\n".join(f.read().splitlines()[-12:])
    except OSError:
        j["log"] = ""
    return j


# ------------------------------------------------------------------ info
def info():
    s = apply_settings()
    hw = config.hardware(s)
    rec = config.recommended(hw["vram_gb"], hw["ram_gb"])
    up = comfy.alive()
    ckpts = comfy.models("checkpoints") if up else []
    llms = llm.installed()
    for m in llms:
        m["fit"] = models.llm_fit(m["size_gb"], hw["vram_gb"], hw["ram_gb"])
    voices = comfy.voices() if up else {}
    own = sorted(os.path.splitext(f)[0] for f in os.listdir(config.VOICES_DIR)
                 if f.lower().endswith((".wav", ".mp3", ".flac")) and not f.startswith("."))
    return {
        "settings": {k: ("********" if k == "hf_token" and v else v) for k, v in s.items()},
        "hardware": hw, "recommended": rec, "quality_levels": list(config.QUALITY), "engines": config.ENGINES,
        "voices": sorted(voices) or sorted(comfy.FALLBACK_VOICES), "own_voices": own,
        "languages": comfy.tts_languages() if up else ["English"],
        "llms": llms, "llm_default": llm.pick_model(s.get("llm"), hw["vram_gb"]),
        "image_models": [c for c in ckpts if not any(x in c.lower() for x in ("ltx", "ace_step", "stable-audio", "stable_audio"))],
        "video_models": [c for c in ckpts if "ltx" in c.lower() and "upscal" not in c.lower()],
        "has_ace": any("ace_step" in c for c in ckpts),
        "has_stable_audio": "stable-audio-open-1.0.safetensors" in ckpts,
        "music_files": media.music_files(), "sfx_builtin": sorted(media.BUILTIN_SFX),
        "sfx_user": sorted(os.path.relpath(p, config.SFX_DIR) for p in media.user_sfx()),
        "comfy_dir": config.find_comfy_dir(s), "font": media.pick_font(s.get("sub_font", "")),
        "ffmpeg": config.ffmpeg_ok(), "root": config.ROOT,
    }


def status():
    s = config.load_settings()
    comfy.configure(s["comfy_url"])
    llm.configure(s["ollama_url"])
    return {"comfy": comfy.alive(), "ollama": llm.alive(), "rendering": running_render(),
            "tasks": busy_tasks()}


def error_report(ref):
    """Text to paste into Claude / Kimi: the error, its traceback, the evidence file and the log tail."""
    found = errors.recent(500, ref=ref)
    if not found:
        raise FactoryError("BAD_REQUEST", f"No error {ref} in logs/errors.jsonl")
    e = found[0]
    c = e.get("ctx") or {}
    out = [f"Ole-K2-Free-Video error report {ref}  (folder {config.ROOT})",
           f"code: {e['code']}   component: {e['component']}   time: {e['time']}",
           f"message: {e['message']}", f"hint: {e['hint']}",
           "where: " + json.dumps(c, default=str), "", "TRACEBACK", e.get("traceback", "")]
    if c.get("detail_file"):
        try:
            with open(os.path.join(config.ROOT, c["detail_file"]), encoding="utf-8", errors="replace") as f:
                out += ["", f"DETAIL FILE {c['detail_file']} (first 4000 chars)", f.read(4000)]
        except OSError:
            pass
    if c.get("project"):
        try:
            with open(projects.path(c["project"], "render.log"), encoding="utf-8", errors="replace") as f:
                out += ["", f"RENDER LOG projects/{c['project']}/render.log (last 30 lines)"] + f.read().splitlines()[-30:]
        except (OSError, ValueError):
            pass
    return "\n".join(out)


def project_view(slug):
    text = projects.load_script(slug)
    sc = scriptfmt.parse(text)
    meta = projects.load_meta(slug)
    v = projects.summary(slug)
    s = config.load_settings()
    est = pipeline.estimate_minutes(sc, pipeline.resolve_options(meta, s), config.hardware(s)) if sc["scenes"] else {}
    v.update(script=text, parsed=sc, check=scriptfmt.check(sc), meta=meta, job=job_of(slug), estimate=est,
             files=projects.scene_files(slug, len(sc["scenes"])),
             estimates=[scriptfmt.estimate(x) for x in sc["scenes"]])
    return v


# ------------------------------------------------------------------ LLM tasks
def _llm_model(body):
    s = config.load_settings()
    hw = config.hardware(s)
    m = body.get("model") or llm.pick_model(s.get("llm"), hw["vram_gb"])
    if not m:
        raise FactoryError("MODEL_MISSING", "No local LLM installed. Pull one on the Models tab (e.g. qwen2.5:7b).")
    return m, int(s.get("llm_ctx", 8192))


def llm_task(kind, body):
    if gpu_busy():
        raise FactoryError("BUSY", "A video is rendering and needs the graphics card. Wait for it or cancel it first.")
    if not llm.alive():
        raise FactoryError("OLLAMA_DOWN", "Ollama is not running. Start it (factory.sh tries) or fix its address in Settings.")
    model, ctx = _llm_model(body)
    voices = sorted(comfy.voices()) or sorted(comfy.FALLBACK_VOICES)

    def fn(t):
        comfy.free_vram()
        t["stage"] = f"Loading {model}"
        stop = lambda: t["cancel"]          # noqa: E731
        on_text = lambda txt: t.update(text=txt[-6000:])  # noqa: E731
        if kind == "write":
            sc, story = llm.write_story(model, body.get("idea", ""), int(body.get("seconds") or 30),
                                        body.get("genre", ""), body.get("narration", "narrator"),
                                        body.get("extra", ""), voices, on_text=on_text,
                                        on_stage=lambda st: t.update(stage=st), should_stop=stop, ctx=ctx)
        elif kind == "convert":
            t["stage"] = "Converting the script"
            sc = llm.convert(model, body.get("text", ""), voices, on_text=on_text, should_stop=stop, ctx=ctx,
                             on_stage=lambda st: t.update(stage=st))
        else:
            t["stage"] = "Improving the script"
            sc = llm.improve(model, body.get("text", ""), body.get("instruction", ""), voices, on_text=on_text,
                             should_stop=stop, ctx=ctx, on_stage=lambda st: t.update(stage=st))
        text = scriptfmt.format_script(sc)
        return {"script": text, "check": scriptfmt.check(sc), "model": model}
    return start_task("llm", fn, f"{kind} with {model}")


def voice_preview(body):
    if gpu_busy():
        raise FactoryError("BUSY", "A video is rendering; try the voice preview afterwards.")
    s = apply_settings()
    if not comfy.alive():
        raise FactoryError("COMFY_DOWN", "ComfyUI is not running.")
    name = body.get("voice") or s["voice"]
    text = (body.get("text") or "Hello! This is how I sound in your next video.")[:300]

    def fn(t):
        llm.unload_all()
        t["stage"] = "Speaking"
        cv = comfy.voices()
        vpath, own = cv.get(name), None
        if not vpath:
            p = [os.path.join(config.VOICES_DIR, name + e) for e in (".wav", ".mp3", ".flac")]
            own = next((x for x in p if os.path.exists(x)), None)
            if not own:
                raise RuntimeError(f"Unknown voice '{name}'")
        d = os.path.join(config.VOICES_DIR, ".preview")
        os.makedirs(d, exist_ok=True)
        raw = comfy.tts(text, os.path.join(d, "raw.flac"), vpath, own_voice_path=own,
                        exaggeration=float(body.get("emotion", s["voice_emotion"])),
                        language=body.get("language") or s["voice_language"])
        out = os.path.join(d, f"{uuid.uuid4().hex[:6]}.wav")
        media.prepare_voice(raw, out, float(body.get("speed", s["voice_speed"])))
        return {"url": "/files/voices/.preview/" + os.path.basename(out)}
    return start_task("voice", fn, f"voice {name}")


# ------------------------------------------------------------------ models
def models_view():
    s = apply_settings()
    hw = config.hardware(s)
    cat = models.catalog_status(comfy, hw)
    for e in cat:
        e["task"] = next((tid for tid, t in TASKS.items() if t["kind"] == "download" and t["label"] == e["id"]
                          and t["state"] == "running"), None)
    llms = llm.installed()
    names = {m["name"] for m in llms}
    for m in llms:
        m["fit"] = models.llm_fit(m["size_gb"], hw["vram_gb"], hw["ram_gb"])
    sugg = [{"name": n, "size_gb": g, "note": note, "installed": n in names,
             "fit": models.llm_fit(g, hw["vram_gb"], hw["ram_gb"])} for n, g, note in models.LLM_SUGGESTIONS]
    return {"hardware": hw, "catalog": cat, "llms": llms, "llm_suggestions": sugg,
            "comfy_models_dir": models.comfy_models_dir(), "ollama": llm.alive()}


def download_task(body):
    s = config.load_settings()
    if body.get("url"):
        entry = models.custom_entry(body["url"], body.get("folder", "checkpoints"), body.get("filename", ""))
    else:
        entry = next((e for e in models.catalog_entries() if e["id"] == body.get("id")), None)
        if not entry:
            raise RuntimeError("Unknown model")
    for t in TASKS.values():
        if t["kind"] == "download" and t["label"] == entry["id"] and t["state"] == "running":
            return t["id"]

    def fn(t):
        def prog(done, total, speed):
            if done < 0:
                t.update(stage="Verifying checksum")
            else:
                t.update(done=done, total=total, speed=speed, stage="Downloading")
        return models.download_entry(entry, s.get("hf_token", ""), prog, lambda: t["cancel"])
    return start_task("download", fn, entry["id"])


def pull_task(name):
    name = name.strip()
    if not re.fullmatch(r"[\w.\-/:]+", name):
        raise RuntimeError("Model name looks wrong (e.g. qwen2.5:7b or hf.co/user/repo:Q4_K_M)")
    apply_settings()

    def fn(t):
        t["stage"] = "Pulling"
        llm.pull(name, lambda d, tot, st: t.update(done=d, total=tot, stage=st), lambda: t["cancel"])
        return {"name": name}
    return start_task("pull", fn, name)


# ------------------------------------------------------------------ uploads / helpers
def save_upload(handler, dest_dir, name, allowed_ext):
    n = int(handler.headers.get("Content-Length") or 0)
    if n <= 0 or n > MAX_UPLOAD:
        raise RuntimeError("Empty or too large file")
    base = re.sub(r"[^\w\-.]+", "_", os.path.basename(name or "file"))[:80] or "file"
    ext = os.path.splitext(base)[1].lower()
    if ext not in allowed_ext:
        raise RuntimeError(f"File type {ext or '?'} is not allowed here")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, base)
    with open(dest + ".part", "wb") as f:
        left = n
        while left:
            chunk = handler.rfile.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    os.replace(dest + ".part", dest)
    return dest


AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm", ".opus"}
MEDIA_EXT = {".mp4", ".mov", ".webm", ".mkv", ".png", ".jpg", ".jpeg", ".webp"}


def open_folder(what):
    if what.startswith("project:"):
        p = projects.path(what.split(":", 1)[1])
    else:
        p = FILE_ROOTS.get(what)
    if not p or not os.path.isdir(p):
        raise RuntimeError("Unknown folder")
    sysname = platform.system()
    if sysname == "Windows":
        os.startfile(p)  # noqa
    elif sysname == "Darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "OleK2Factory/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def _local_origin_ok(self):
        """Refuse cross-site requests (a web page in your browser poking this server)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return urllib.parse.urlparse(origin).netloc == host

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        if not self._local_origin_ok():
            return self._json({"error": "cross-site request refused"}, 403)
        self._route("POST")

    def _route(self, method):
        u = urllib.parse.urlparse(self.path)
        p, q = u.path, dict(urllib.parse.parse_qsl(u.query))
        if p.startswith("/api/"):
            try:
                with LOCK:
                    maybe_reload()
            except Exception:
                pass
        try:
            if method == "GET" and (p == "/" or p.startswith("/web/")):
                return self._static(os.path.join(config.WEB_DIR, "index.html" if p == "/" else p[5:]), config.WEB_DIR)
            if method == "GET" and p.startswith("/files/"):
                root, _, rest = p[7:].partition("/")
                if root not in FILE_ROOTS:
                    return self._json({"error": "not found"}, 404)
                return self._static(os.path.join(FILE_ROOTS[root], urllib.parse.unquote(rest)), FILE_ROOTS[root])
            out = self._api(method, p, q)
            if out is None:
                return self._json({"error": "not found"}, 404)
            return self._json(out)
        except FactoryError as e:
            body = {"error": str(e), "code": e.code, "hint": e.hint}
            if e.code not in ("BUSY", "BAD_REQUEST", "CANCELLED"):
                body["ref"] = errors.record(e, "api", path=p)["ref"]
            return self._json(body, 400)
        except (RuntimeError, ValueError, KeyError) as e:
            return self._json({"error": str(e), "code": "BAD_REQUEST", "hint": ""}, 400)
        except Exception as e:
            entry = errors.record(e, "api", path=p, method=method)
            return self._json({"error": f"{type(e).__name__}: {e}", "code": entry["code"], "hint": entry["hint"],
                               "ref": entry["ref"]}, 500)

    def _api(self, method, p, q):
        G, P = method == "GET", method == "POST"
        if G and p == "/api/status":
            return status()
        if G and p == "/api/info":
            return info()
        if G and p == "/api/errors":
            return {"errors": [errors.public(e) for e in errors.recent(int(q.get("n", 20)))]}
        m = re.fullmatch(r"/api/errors/(E[\w-]+)/report", p)
        if G and m:
            return {"text": error_report(m.group(1))}
        if P and p == "/api/settings":
            body = self._body()
            if body.get("hf_token") == "********":
                body.pop("hf_token")
            s = config.save_settings(body)
            apply_settings(s)
            return {"ok": True}
        if G and p == "/api/hardware/detect":
            return config.detect_hardware()
        if G and p == "/api/templates":
            def rd(*a):
                try:
                    with open(os.path.join(config.ROOT, *a), encoding="utf-8") as f:
                        return f.read()
                except OSError:
                    return ""
            return {"blank": rd("templates", "video-template.txt"), "chatgpt": rd("templates", "copy-to-chatgpt.txt"),
                    "example": rd("examples", "lighthouse.txt")}
        if P and p == "/api/script/parse":
            sc = scriptfmt.parse(self._body().get("text", ""))
            return {"parsed": sc, "check": scriptfmt.check(sc), "script": scriptfmt.format_script(sc),
                    "estimates": [scriptfmt.estimate(x) for x in sc["scenes"]]}
        if P and p == "/api/script/format":
            given = self._body().get("parsed") or {}
            sc = scriptfmt.empty()
            sc.update({k: v for k, v in given.items() if k in sc})
            sc["scenes"] = [dict({"seconds": 0, "visual": "", "motion": "", "voice": "", "speaker": "", "text": "",
                                  "sound": "", "chapter": -1}, **x) for x in sc["scenes"] if isinstance(x, dict)]
            text = scriptfmt.format_script(scriptfmt.normalize(sc))
            return {"script": scriptfmt.format_script(scriptfmt.parse(text))}
        if P and p == "/api/script/template":
            b = self._body()
            voices = sorted(comfy.voices()) or None
            return {"text": scriptfmt.template(b.get("idea", ""), int(b.get("seconds") or 30), b.get("genre", ""),
                                               voices, b.get("narration", "narrator"))}
        if P and p == "/api/llm/write":
            return {"task": llm_task("write", self._body())}
        if P and p == "/api/llm/convert":
            return {"task": llm_task("convert", self._body())}
        if P and p == "/api/llm/improve":
            return {"task": llm_task("improve", self._body())}
        m = re.fullmatch(r"/api/tasks/(\w+)(/cancel)?", p)
        if m:
            t = TASKS.get(m.group(1))
            if not t:
                raise RuntimeError("Task not found")
            if m.group(2) and P:
                t["cancel"] = True
            return task_view(t)
        if G and p == "/api/projects":
            return {"projects": projects.all_projects()}
        if P and p == "/api/projects":
            b = self._body()
            return {"slug": projects.create(b.get("text", ""), b.get("name", ""), b.get("output_name", ""))}
        m = re.fullmatch(r"/api/projects/([a-z0-9\-]+)(?:/(\w+))?", p)
        if m:
            return self._project(method, m.group(1), m.group(2) or "", q)
        if G and p == "/api/models":
            return models_view()
        if P and p == "/api/models/download":
            return {"task": download_task(self._body())}
        if P and p == "/api/ollama/pull":
            return {"task": pull_task(self._body().get("name", ""))}
        if P and p == "/api/ollama/delete":
            apply_settings()
            llm.delete(self._body().get("name", ""))
            return {"ok": True}
        if G and p == "/api/hf/search":
            return {"results": models.hf_search_gguf(q.get("q", ""))}
        if G and p == "/api/hf/files":
            hw = config.hardware()
            return {"files": models.hf_gguf_files(q.get("repo", ""), hw["vram_gb"], hw["ram_gb"])}
        if P and p == "/api/voice/preview":
            return {"task": voice_preview(self._body())}
        if P and p == "/api/voices/upload":
            name = re.sub(r"[^\w\-]+", "_", q.get("name", "my-voice"))[:40] or "my-voice"
            src = save_upload(self, os.path.join(config.VOICES_DIR, ".incoming"), q.get("file", "rec.webm"), AUDIO_EXT)
            dest = os.path.join(config.VOICES_DIR, name + ".wav")
            media.run(["-i", src, "-t", 30, "-af", "highpass=f=70,loudnorm=I=-18", "-ac", 1, "-ar", 44100, dest])
            os.remove(src)
            return {"voice": name}
        if P and p == "/api/music/upload":
            return {"file": os.path.basename(save_upload(self, config.MUSIC_DIR, q.get("name", ""), AUDIO_EXT - {".webm", ".opus"}))}
        if P and p == "/api/sfx/upload":
            return {"file": os.path.basename(save_upload(self, config.SFX_DIR, q.get("name", ""), AUDIO_EXT - {".webm", ".opus"}))}
        if P and p == "/api/open":
            return {"opened": open_folder(self._body().get("what", ""))}
        return None

    def _project(self, method, slug, action, q):
        if not os.path.isdir(projects.path(slug)):
            raise RuntimeError("Project not found")
        if method == "GET" and not action:
            return project_view(slug)
        if method != "POST":
            return None
        if action == "script":
            projects.save_script(slug, self._body().get("text", ""))
            return project_view(slug)
        if action == "meta":
            b = self._body()
            new = projects.rename(slug, b.get("name"), b.get("output_name"))
            meta = projects.load_meta(new)
            if isinstance(b.get("options"), dict):
                meta["options"] = {k: v for k, v in b["options"].items() if k in config.DEFAULTS or k == "voice_override"}
            if isinstance(b.get("scenes"), dict):
                meta["scenes"] = b["scenes"]
            projects.save_meta(new, meta)
            return {"slug": new}
        if action == "render":
            start_render(slug, self._body().get("scene"))
            return {"ok": True}
        if action == "cancel":
            cancel_render(slug)
            return {"ok": True}
        if action == "trash":
            if RENDER["slug"] == slug and gpu_busy():
                raise FactoryError("BUSY", "Cancel the render first")
            projects.trash(slug)
            return {"ok": True}
        if action == "posted":
            meta = projects.load_meta(slug)
            meta["posted"] = time.strftime("%Y-%m-%d") if self._body().get("posted") else ""
            projects.save_meta(slug, meta)
            return {"ok": True}
        if action == "media":
            scene = int(q.get("scene", 0))
            dest = save_upload(self, projects.path(slug, "media"), f"s{scene:02d}_" + q.get("name", "clip.mp4"), MEDIA_EXT)
            meta = projects.load_meta(slug)
            meta["scenes"].setdefault(str(scene), {})["media"] = os.path.relpath(dest, projects.path(slug))
            meta["scenes"][str(scene)]["engine"] = "own"
            projects.save_meta(slug, meta)
            return {"media": meta["scenes"][str(scene)]["media"]}
        return None

    def _static(self, path, root):
        real, base = os.path.realpath(path), os.path.realpath(root)
        if not (real == base or real.startswith(base + os.sep)) or not os.path.isfile(real):
            return self._json({"error": "not found"}, 404)
        size = os.path.getsize(real)
        ctype = mimetypes.guess_type(real)[0] or "application/octet-stream"
        if real.endswith((".txt", ".ass")):
            ctype = "text/plain; charset=utf-8"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2) or 0))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(real, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def main():
    s = config.load_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(s.get("port", 8770)))
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 lets other PCs on your network open the GUI (no password!)")
    a = ap.parse_args()
    apply_settings(s)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"Ole-K2-Free-Video GUI: http://{'127.0.0.1' if a.host in ('0.0.0.0', '') else a.host}:{a.port}", flush=True)
    if a.host not in ("127.0.0.1", "localhost"):
        print("WARNING: the GUI is open to your network without a password.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

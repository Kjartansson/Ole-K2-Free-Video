#!/usr/bin/env python3
"""Ole-K2-Free-Video: local TikTok video factory. No cloud, no tokens.

    python3 factory.py                       start ComfyUI (if needed) + the web GUI, open the browser
    python3 factory.py make story.txt        text file -> finished video (no GUI)
    python3 factory.py write "idea" out.txt  the local LLM writes a script file
    python3 factory.py doctor                check this PC: what is installed, what is missing
    python3 factory.py shortcut              add a desktop/app-menu launcher (Linux)

factory.sh (Linux/macOS) and factory.bat (Windows) just call this file.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "app"))
if sys.version_info < (3, 9):
    sys.exit("Python 3.9 or newer is needed (you have %d.%d)." % sys.version_info[:2])

import comfy     # noqa: E402
import config    # noqa: E402
import llm       # noqa: E402

STARTED = {"comfy": None}


def say(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ services
def comfy_command(cdir, url):
    port = urllib.parse.urlparse(url).port or 8188
    if os.path.isfile(os.path.join(cdir, "start.sh")) and platform.system() != "Windows":
        return ["bash", os.path.join(cdir, "start.sh")]
    cands = [os.path.join(cdir, "venv", "bin", "python"), os.path.join(cdir, ".venv", "bin", "python"),
             os.path.join(cdir, "venv", "Scripts", "python.exe"), os.path.join(cdir, ".venv", "Scripts", "python.exe"),
             os.path.join(os.path.dirname(cdir), "python_embeded", "python.exe")]
    py = next((p for p in cands if os.path.isfile(p)), sys.executable)
    return [py, "-s", os.path.join(cdir, "main.py"), "--listen", "127.0.0.1", "--port", str(port)]


def ensure_comfy(s, wait=180):
    comfy.configure(s["comfy_url"])
    if comfy.alive():
        return True
    host = urllib.parse.urlparse(s["comfy_url"]).hostname
    cdir = config.find_comfy_dir(s)
    if host not in ("127.0.0.1", "localhost") or not cdir or not s.get("comfy_autostart", True):
        say(f"! ComfyUI is not reachable at {s['comfy_url']}" + ("" if cdir else " and no local ComfyUI folder was found"))
        return False
    cmd = comfy_command(cdir, s["comfy_url"])
    log = open(os.path.join(config.LOGS_DIR, "comfyui.log"), "a")
    say(f"Starting ComfyUI ({cdir}) ... log: logs/comfyui.log")
    kw = {"creationflags": 0x00000200} if platform.system() == "Windows" else {"start_new_session": True}
    STARTED["comfy"] = subprocess.Popen(cmd, cwd=cdir, stdout=log, stderr=subprocess.STDOUT, **kw)
    t0 = time.time()
    while time.time() - t0 < wait:
        if comfy.alive():
            say(f"ComfyUI is up ({time.time() - t0:.0f} s)")
            return True
        if STARTED["comfy"].poll() is not None:
            say("! ComfyUI stopped while starting. See logs/comfyui.log")
            return False
        time.sleep(2)
    say("! ComfyUI did not answer in time. See logs/comfyui.log")
    return False


def ensure_ollama(s):
    llm.configure(s["ollama_url"])
    if llm.alive():
        return True
    host = urllib.parse.urlparse(s["ollama_url"]).hostname
    exe = shutil.which("ollama")
    if host in ("127.0.0.1", "localhost") and exe:
        say("Starting Ollama ...")
        log = open(os.path.join(config.LOGS_DIR, "ollama.log"), "a")
        kw = {"creationflags": 0x00000200} if platform.system() == "Windows" else {"start_new_session": True}
        subprocess.Popen([exe, "serve"], stdout=log, stderr=subprocess.STDOUT, **kw)
        for _ in range(20):
            time.sleep(1)
            if llm.alive():
                return True
    say(f"! Ollama is not reachable at {s['ollama_url']} (story writing with the local AI will not work)")
    return False


def stop_started():
    p = STARTED["comfy"]
    if p and p.poll() is None:
        say("Stopping the ComfyUI this launcher started ...")
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()


# ------------------------------------------------------------------ commands
GUI_PID = os.path.join(config.LOGS_DIR, "gui.pid")


def _port_owner(port):
    """None = free, 'factory' = our GUI already answers there, 'other' = another program has it."""
    import json
    import socket
    with socket.socket() as sk:
        sk.settimeout(1)
        if sk.connect_ex(("127.0.0.1", port)) != 0:
            return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
            return "factory" if "comfy" in json.loads(r.read()) else "other"
    except Exception:
        return "other"


def _gui_pid():
    try:
        with open(GUI_PID) as f:
            pid, port = f.read().split()
        return int(pid), int(port)
    except (OSError, ValueError):
        return None, None


def _pid_on_port(port):
    """Process listening on the port (for a GUI started before gui.pid existed). Linux/macOS."""
    import re
    for cmd in (["ss", "-ltnpH", f"sport = :{port}"], ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]):
        if shutil.which(cmd[0]):
            out = subprocess.run(cmd, capture_output=True, text=True).stdout
            m = re.search(r"pid=(\d+)", out) or re.search(r"^(\d+)$", out.strip(), re.M)
            if m:
                return int(m.group(1))
    return None


def cmd_stop(a, quiet=False):
    """Stop the running GUI (like Ctrl+C in its window). Renders keep going: they run on their own."""
    pid, port = _gui_pid()
    if not pid:
        port = getattr(a, "port", 0) or int(config.load_settings().get("port", 8770))
        if _port_owner(port) == "factory":
            pid = _pid_on_port(port)
    if not pid:
        if not quiet:
            say("No factory GUI found (logs/gui.pid). If one still runs in another window, press Ctrl+C there.")
        return False
    import signal
    try:
        os.kill(pid, signal.SIGINT if platform.system() != "Windows" else signal.SIGTERM)
    except OSError:
        say(f"The factory GUI (pid {pid}) was not running any more.")
        os.remove(GUI_PID)
        return False
    for _ in range(40):
        if not _port_owner(port):
            break
        time.sleep(0.25)
    say(f"Stopped the factory GUI (pid {pid}, port {port}). Renders that were running keep going.")
    return True


def cmd_restart(a):
    cmd_stop(a, quiet=True)
    cmd_gui(a)


def cmd_gui(a):
    s = config.load_settings()
    ok, why = config.ffmpeg_ok()
    if not ok:
        say(f"! {why}. Install ffmpeg first (see INSTALL.md).")
    port = a.port or int(s.get("port", 8770))
    owner = _port_owner(port)
    if owner == "factory":
        url = f"http://127.0.0.1:{port}"
        say(f"\n  The factory is already running: {url} (opening it in the browser).\n"
            f"  To restart it with the newest code: ./factory.sh restart\n")
        if not a.no_browser:
            webbrowser.open(url)
        return
    if owner == "other":
        free = next((p for p in range(port + 1, port + 30) if not _port_owner(p)), None)
        if not free:
            sys.exit(f"Port {port} and the next ones are taken by other programs. Start with --port <number>.")
        say(f"  Port {port} is used by another program; using {free} instead.")
        port = free
    if not a.no_comfy:
        threading.Thread(target=ensure_comfy, args=(s,), daemon=True).start()
    threading.Thread(target=ensure_ollama, args=(s,), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    say(f"\n  Ole-K2 Free Video is running:  {url}\n  Close this window or press Ctrl+C to stop.\n")
    sys.argv = [sys.argv[0], "--port", str(port), "--host", a.host]
    import server
    with open(GUI_PID, "w") as f:
        f.write(f"{os.getpid()} {port}")
    try:
        server.main()
    finally:
        try:
            if _gui_pid()[0] == os.getpid():
                os.remove(GUI_PID)
        except OSError:
            pass
        stop_started()


def cmd_make(a):
    s = config.load_settings()
    if not ensure_comfy(s):
        sys.exit("ComfyUI is needed to make pictures and voices. Start it, or check Settings / INSTALL.md.")
    import pipeline
    try:
        pipeline.make(a.file, a.engine, a.quality, a.voice, a.music, a.name, a.output)
    finally:
        stop_started()


def cmd_write(a):
    s = config.load_settings()
    if not ensure_ollama(s):
        sys.exit(1)
    import scriptfmt
    hw = config.hardware(s)
    model = a.model or llm.pick_model(s.get("llm"), hw["vram_gb"])
    if not model:
        sys.exit("No local LLM installed: run  ollama pull qwen2.5:7b  (or use the Models tab).")
    comfy.configure(s["comfy_url"])
    comfy.free_vram()
    voices = sorted(comfy.voices()) or sorted(comfy.FALLBACK_VOICES)
    say(f"Writing with {model} ...")
    sc, _ = llm.write_story(model, a.idea, a.seconds, a.genre, a.narration, a.extra, voices,
                            on_stage=lambda st: say("  " + st), ctx=int(s.get("llm_ctx", 8192)))
    text = scriptfmt.format_script(sc)
    out = a.out or (sc["title"] or "story").lower().replace(" ", "-")[:40] + ".txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    say(text)
    say(f"Saved: {out}\nNext:  ./factory.sh make {out}")


def doctor_text():
    """The doctor table as text (also part of `report`)."""
    s = config.load_settings()
    rows = []

    def row(name, ok, detail, fix=""):
        rows.append((("OK  " if ok else ("--  " if ok is None else "MISS")), name, detail, "" if ok else fix))

    row("Python", True, platform.python_version())
    ok, why = config.ffmpeg_ok()
    row("ffmpeg + libass", ok, why if not ok else shutil.which("ffmpeg"), "install ffmpeg (INSTALL.md)")
    row("ffprobe", bool(shutil.which("ffprobe")), shutil.which("ffprobe") or "not found", "comes with ffmpeg")
    comfy.configure(s["comfy_url"])
    up = comfy.alive()
    cdir = config.find_comfy_dir(s)
    row("ComfyUI folder", bool(cdir), cdir or "not found", "install ComfyUI or set its folder in Settings")
    row("ComfyUI running", up if up else None, s["comfy_url"] if up else "not running (the GUI starts it)")
    if up:
        row("TTS-Audio-Suite node", comfy.has_node("UnifiedTTSTextNode"), "voices",
            "ComfyUI-Manager > install 'TTS Audio Suite' (INSTALL.md)")
        row("LTX nodes", comfy.has_node("LTXVImgToVideo"), "video", "update ComfyUI")
        ck = comfy.models("checkpoints")
        img = [c for c in ck if not any(x in c.lower() for x in ("ltx", "ace_step", "stable-audio"))]
        row("Image model (SDXL)", bool(img), ", ".join(img) or "none", "Models tab: SDXL base 1.0")
        row("LTX video model", any("ltx" in c.lower() for c in ck), ", ".join(c for c in ck if "ltx" in c.lower()) or "none",
            "Models tab: LTX-Video 2B (or use the 'Image + camera move' engine)")
        row("T5 encoder (LTX)", "t5xxl_fp8_e4m3fn_scaled.safetensors" in comfy.models("text_encoders"), "text_encoders",
            "Models tab: T5-XXL text encoder")
    llm.configure(s["ollama_url"])
    lu = llm.alive()
    row("Ollama", lu, s["ollama_url"] if lu else "not reachable", "install from ollama.com")
    if lu:
        ms = llm.installed()
        row("Story LLM", bool(ms), ", ".join(m["name"] for m in ms[:6]) or "none", "ollama pull qwen2.5:7b")
    hw = config.hardware(s)
    row("GPU", bool(hw["vram_gb"]), f"{hw['gpu'] or '?'} {hw['vram_gb']} GB VRAM, {hw['ram_gb']} GB RAM",
        "NVIDIA card + driver recommended")
    rec = config.recommended(hw["vram_gb"], hw["ram_gb"])
    w = max(len(r[1]) for r in rows)
    out = [""]
    for st, name, detail, fix in rows:
        out.append(f" {st} {name.ljust(w)}  {detail}" + (f"\n      -> {fix}" if fix else ""))
    out.append(f"\n Suggested for this PC: quality '{rec['quality']}', engine '{rec['engine']}', "
               f"LLMs up to ~{rec['llm_max_gb']} GB.\n")
    return "\n".join(out)


def cmd_doctor(a):
    say(doctor_text())


def cmd_errors(a):
    """Compact list of recent errors (newest first). --ref shows one in full."""
    import errors
    if a.ref:
        found = errors.recent(1000, ref=a.ref)
        if not found:
            sys.exit(f"No error {a.ref} in logs/errors.jsonl")
        import json
        say(json.dumps(found[0], indent=1, default=str))
        return
    items = errors.recent(a.n)
    if not items:
        say("No errors recorded (logs/errors.jsonl is empty).")
        return
    for e in items:
        say(errors.one_line(e))
    say(f"\nFull entry: ./factory.sh errors --ref <ref>     Everything in one file: ./factory.sh report")


def _tail(path, n):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return [f"({os.path.relpath(path, ROOT)} not found)"]


def cmd_report(a):
    """One file with everything an agent (Claude, Kimi) needs to find a problem: read only this file."""
    import datetime
    import errors
    import projects
    out = [f"Ole-K2-Free-Video report {datetime.datetime.now():%Y-%m-%d %H:%M}  folder {ROOT}",
           f"git: {subprocess.run(['git', '-C', ROOT, 'log', '-1', '--format=%h %s'], capture_output=True, text=True).stdout.strip()}",
           "", "== DOCTOR", doctor_text(), "== RECENT ERRORS (newest first)"]
    items = errors.recent(20)
    out += [errors.one_line(e) for e in items] or ["(none)"]
    for e in items[:3]:
        out += ["", f"== ERROR {e['ref']} IN FULL", f"code {e['code']} | {e['component']} | {e['time']}",
                f"message: {e['message']}", f"hint: {e['hint']}", f"ctx: {e.get('ctx')}", e.get("traceback", "")]
        det = (e.get("ctx") or {}).get("detail_file")
        if det:
            try:
                with open(os.path.join(ROOT, det), encoding="utf-8", errors="replace") as f:
                    out += [f"-- detail file {det} (first 3000 chars)", f.read(3000)]
            except OSError:
                pass
    projs = projects.all_projects()
    if projs:
        p = max(projs, key=lambda x: os.path.getmtime(projects.path(x["slug"], "job.json"))
                if os.path.exists(projects.path(x["slug"], "job.json")) else 0)
        out += ["", f"== LAST RENDER: projects/{p['slug']} job={projects.job(p['slug'])}",
                *[l for l in _tail(projects.path(p["slug"], "render.log"), 80) if not l.startswith("$ ffmpeg")][-40:]]
    out += ["", "== logs/comfyui.log (last 40 lines)", *_tail(os.path.join(config.LOGS_DIR, "comfyui.log"), 40),
            "", "== logs/debug.log (ignored cleanup errors, last 15)", *_tail(errors.DEBUG_LOG, 15)]
    path = os.path.join(config.LOGS_DIR, f"report-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in out) + "\n")
    say(f"Report written: {os.path.relpath(path, ROOT)}  ({len(items)} recent errors)")
    say("Give this ONE file to Claude or Kimi: 'read only this file and find the problem'.")


def cmd_shortcut(a):
    if platform.system() != "Linux":
        sys.exit("On Windows: right-click factory.bat > Send to > Desktop (create shortcut).")
    d = os.path.expanduser("~/.local/share/applications")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "ole-k2-free-video.desktop")
    with open(p, "w") as f:
        f.write(f"""[Desktop Entry]
Type=Application
Name=Ole-K2 Free Video
Comment=Local TikTok video factory
Exec=bash -c 'cd "{ROOT}" && ./factory.sh'
Terminal=true
Icon=video-x-generic
Categories=AudioVideo;Video;
""")
    say(f"Launcher added: {p} (search 'Ole-K2' in the app menu)")


def main():
    ap = argparse.ArgumentParser(description="Ole-K2-Free-Video: local TikTok video factory")
    sub = ap.add_subparsers(dest="cmd")
    g = sub.add_parser("gui", help="start the web GUI (default)")
    for p in (ap, g):
        p.add_argument("--port", type=int, default=0)
        p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 = reachable from other PCs (no password)")
        p.add_argument("--no-browser", action="store_true")
        p.add_argument("--no-comfy", action="store_true", help="do not start ComfyUI")
    m = sub.add_parser("make", help="script file -> finished video")
    m.add_argument("file")
    m.add_argument("--engine", default="", help="ltx_i2v | still | ltx_t2v | wan")
    m.add_argument("--quality", default="", help="draft | standard | high | max")
    m.add_argument("--voice", default="", help="narrator voice, e.g. male_01")
    m.add_argument("--music", default="", help="builtin | file | ace | none")
    m.add_argument("--name", default="", help="project name")
    m.add_argument("--output", default="", help="output file name (in exports/)")
    w = sub.add_parser("write", help="local LLM writes a script file from an idea")
    w.add_argument("idea")
    w.add_argument("out", nargs="?", default="")
    w.add_argument("--seconds", type=int, default=30)
    w.add_argument("--genre", default="")
    w.add_argument("--narration", default="narrator", help="narrator | mixed | dialogue | none")
    w.add_argument("--extra", default="")
    w.add_argument("--model", default="")
    sub.add_parser("doctor", help="check what is installed")
    er = sub.add_parser("errors", help="list recent errors (logs/errors.jsonl)")
    er.add_argument("-n", type=int, default=15)
    er.add_argument("--ref", default="", help="show one error in full")
    sub.add_parser("report", help="write one file with everything needed to debug a problem")
    sub.add_parser("shortcut", help="add an app-menu launcher (Linux)")
    sub.add_parser("stop", help="stop the running GUI (renders keep going)")
    rs = sub.add_parser("restart", help="stop the running GUI and start it again with the newest code")
    for p in (rs,):
        p.add_argument("--port", type=int, default=0)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--no-browser", action="store_true")
        p.add_argument("--no-comfy", action="store_true")
    a = ap.parse_args()
    {"make": cmd_make, "write": cmd_write, "doctor": cmd_doctor, "errors": cmd_errors, "report": cmd_report,
     "shortcut": cmd_shortcut, "stop": cmd_stop, "restart": cmd_restart}.get(a.cmd, cmd_gui)(a)


if __name__ == "__main__":
    main()

"""Starting the helper programs (ComfyUI, Ollama) on this PC. Shared by the launcher (factory.py)
and the GUI server, so a factory that is already running can start ComfyUI when it is needed
(Render, or a click on the ComfyUI dot) instead of failing with COMFY_DOWN."""
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

import comfy
import config
import llm

STARTED = {"comfy": None}      # the ComfyUI process this program started (stopped again on exit)
STATE = {"comfy": ""}          # "" | "starting" | "failed: <why>"  (shown in the GUI)
_LOCK = threading.Lock()


def _detached():
    return {"creationflags": 0x00000200} if platform.system() == "Windows" else {"start_new_session": True}


def comfy_command(cdir, url):
    port = urllib.parse.urlparse(url).port or 8188
    if os.path.isfile(os.path.join(cdir, "start.sh")) and platform.system() != "Windows":
        return ["bash", os.path.join(cdir, "start.sh")]
    cands = [os.path.join(cdir, "venv", "bin", "python"), os.path.join(cdir, ".venv", "bin", "python"),
             os.path.join(cdir, "venv", "Scripts", "python.exe"), os.path.join(cdir, ".venv", "Scripts", "python.exe"),
             os.path.join(os.path.dirname(cdir), "python_embeded", "python.exe")]
    py = next((p for p in cands if os.path.isfile(p)), sys.executable)
    return [py, "-s", os.path.join(cdir, "main.py"), "--listen", "127.0.0.1", "--port", str(port)]


def why_not_startable(s):
    """'' if this program can start ComfyUI itself, else the reason (in plain words)."""
    host = urllib.parse.urlparse(s["comfy_url"]).hostname
    if host not in ("127.0.0.1", "localhost"):
        return f"ComfyUI is set to run on another PC ({s['comfy_url']}): start it there."
    if not s.get("comfy_autostart", True):
        return "Starting ComfyUI automatically is switched off (Settings > Connections)."
    if not config.find_comfy_dir(s):
        return "No ComfyUI folder found on this PC (Settings > Connections > ComfyUI folder)."
    return ""


def start_comfy(s=None, wait=180, say=print):
    """Make sure ComfyUI answers; start it if it is local and allowed. One start at a time: a second
    caller waits for the first. Returns True when ComfyUI answers."""
    s = s or config.load_settings()
    comfy.configure(s["comfy_url"])
    if comfy.alive():
        STATE["comfy"] = ""
        return True
    with _LOCK:
        if comfy.alive():
            STATE["comfy"] = ""
            return True
        why = why_not_startable(s)
        if why:
            STATE["comfy"] = "failed: " + why
            say(f"! ComfyUI is not reachable at {s['comfy_url']}. {why}")
            return False
        p = STARTED["comfy"]
        if p is None or p.poll() is not None:
            cdir = config.find_comfy_dir(s)
            say(f"Starting ComfyUI ({cdir}) ... log: logs/comfyui.log")
            log = open(os.path.join(config.LOGS_DIR, "comfyui.log"), "a")
            p = STARTED["comfy"] = subprocess.Popen(comfy_command(cdir, s["comfy_url"]), cwd=cdir, stdout=log,
                                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                                     **_detached())
        STATE["comfy"] = "starting"
        t0 = time.time()
        while time.time() - t0 < wait:
            if comfy.alive():
                say(f"ComfyUI is up ({time.time() - t0:.0f} s)")
                STATE["comfy"] = ""
                return True
            if p.poll() is not None:
                STATE["comfy"] = "failed: ComfyUI stopped while starting (see logs/comfyui.log)"
                say("! ComfyUI stopped while starting. See logs/comfyui.log")
                return False
            time.sleep(2)
        STATE["comfy"] = "failed: ComfyUI did not answer in time (see logs/comfyui.log)"
        say("! ComfyUI did not answer in time. See logs/comfyui.log")
        return False


def ensure_ollama(s=None, say=print):
    s = s or config.load_settings()
    llm.configure(s["ollama_url"])
    if llm.alive():
        return True
    host = urllib.parse.urlparse(s["ollama_url"]).hostname
    exe = shutil.which("ollama")
    if host in ("127.0.0.1", "localhost") and exe:
        say("Starting Ollama ...")
        log = open(os.path.join(config.LOGS_DIR, "ollama.log"), "a")
        subprocess.Popen([exe, "serve"], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **_detached())
        for _ in range(20):
            time.sleep(1)
            if llm.alive():
                return True
    say(f"! Ollama is not reachable at {s['ollama_url']} (story writing with the local AI will not work)")
    return False


def stop_started(say=print):
    """Stop the ComfyUI this program started (frees the graphics card when the factory closes)."""
    p = STARTED["comfy"]
    if p and p.poll() is None:
        say("Stopping the ComfyUI this launcher started ...")
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()

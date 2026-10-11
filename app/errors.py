"""Errors that explain themselves, for Ole and for agents (Claude, Kimi) reading the logs.

Every failure becomes a FactoryError with
  code    short, stable name (GPU_OOM, COMFY_DOWN, FFMPEG, ...) to search for,
  hint    what to do, in plain words (shown in the GUI),
  ctx     where it happened (project, stage, shot, node, command ...),
and record() appends ONE JSON line to logs/errors.jsonl with a `ref` (e.g. E2210-4f3a) that the GUI
shows, plus the traceback. Big evidence (the failing ffmpeg command + output, the ComfyUI workflow,
the LLM's raw answer) goes to logs/fail/<ref-ish>.txt and the line points to it (`detail_file`).

Agents: start with `./factory.sh errors` (compact list) or `./factory.sh report` (one file with
everything), not with the raw logs.
"""
import datetime
import json
import os
import re
import traceback
import uuid

import config

ERR_LOG = os.path.join(config.LOGS_DIR, "errors.jsonl")
FAIL_DIR = os.path.join(config.LOGS_DIR, "fail")
DEBUG_LOG = os.path.join(config.LOGS_DIR, "debug.log")

HINTS = {
    "COMFY_DOWN": "ComfyUI is not running or not reachable. Start the factory with ./factory.sh (it starts "
                  "ComfyUI) or check the address in Settings > Connections. ComfyUI's own log: logs/comfyui.log",
    "COMFY_NODE": "A step inside ComfyUI failed. The node and its error are in the detail file, together with the "
                  "workflow, so it can be re-run in ComfyUI. ComfyUI's own log: logs/comfyui.log",
    "COMFY_REFUSED": "ComfyUI refused the job before starting (usually a model or node it does not have). "
                     "Run ./factory.sh doctor.",
    "GPU_OOM": "The graphics card ran out of memory. Choose quality 'draft' or the engine 'Image + camera move', "
               "or close other programs that use the GPU. Then render again: finished parts are kept.",
    "MODEL_MISSING": "A model is missing. Download it on the Models tab, or run ./factory.sh doctor to see which.",
    "NODE_MISSING": "A ComfyUI node pack is missing (see INSTALL.md).",
    "FFMPEG": "ffmpeg failed while cutting or mixing. The exact command and ffmpeg's output are in the detail file.",
    "OLLAMA_DOWN": "Ollama is not running. Start it (ollama serve, or ./factory.sh starts it) or check "
                   "Settings > Connections.",
    "LLM_OUTPUT": "The local LLM gave an answer the factory cannot use. Try again or pick a bigger model on the "
                  "Models tab. Its raw answer is in the detail file.",
    "SCRIPT": "The script cannot be used as it is. Open the Story tab and click Check.",
    "OWN_MEDIA": "A scene is set to 'my own clip' but its file is missing. Upload it again on the Scenes tab.",
    "DISK_FULL": "The disk is full. Free some space (the work/ folders inside old projects can be deleted).",
    "DOWNLOAD": "The download failed. Click Download again: it continues where it stopped.",
    "TIMEOUT": "A step took too long and was stopped. Try a lower quality or a shorter scene.",
    "BUSY": "Something else is using the graphics card right now. Wait for it to finish or cancel it.",
    "CANCELLED": "Stopped on request.",
    "BAD_REQUEST": "The request was not valid.",
    "UNKNOWN": "Unexpected error. The traceback is in logs/errors.jsonl under this ref; "
               "./factory.sh report bundles everything for Claude or Kimi.",
}

# message pattern -> code, checked in order (first match wins)
PATTERNS = [
    (r"out of memory|OutOfMemory|Allocation on device|CUBLAS_STATUS_ALLOC_FAILED|cudaMalloc", "GPU_OOM"),
    (r"No space left on device|Disk quota exceeded", "DISK_FULL"),
    (r"cancelled|Cancelled", "CANCELLED"),
    (r"took longer than|timed out|TimeoutExpired|Read timed out", "TIMEOUT"),
    (r"ffmpeg failed|ffprobe", "FFMPEG"),
    (r"value_not_in_list|not in list|No image model|is missing|not downloaded|model .* not found|"
     r"pull model manifest|file does not exist", "MODEL_MISSING"),
    (r"node pack is missing|Cannot find node|class_type .* not found|missing_node_type", "NODE_MISSING"),
    (r"own clip", "OWN_MEDIA"),
]


class FactoryError(RuntimeError):
    def __init__(self, code, message, hint="", **ctx):
        super().__init__(message)
        self.code = code if code in HINTS else "UNKNOWN"
        self.hint = hint or HINTS.get(self.code, "")
        self.ctx = {k: v for k, v in ctx.items() if v not in (None, "")}


def classify(exc, component=""):
    """Code for any exception (FactoryError keeps its own)."""
    if isinstance(exc, FactoryError):
        return exc.code
    msg = f"{type(exc).__name__}: {exc}"
    for pat, code in PATTERNS:
        if re.search(pat, msg, re.I):
            return code
    if ("download" in component or "pull" in component) and isinstance(exc, (ConnectionError, OSError)):
        return "DOWNLOAD"
    if isinstance(exc, (ConnectionError, OSError)) and re.search(
            r"refused|unreachable|reset|Errno 111|Errno 113|Name or service|urlopen error|Remote end closed", msg, re.I):
        return "OLLAMA_DOWN" if "llm" in component or "ollama" in component else "COMFY_DOWN"
    if isinstance(exc, (ValueError, KeyError)) and component.startswith("api"):
        return "BAD_REQUEST"
    return "UNKNOWN"


def new_ref():
    return f"E{datetime.datetime.now():%H%M}-{uuid.uuid4().hex[:4]}"


def save_detail(kind, text, ref=None):
    """Big evidence (command + output, workflow, raw LLM answer) -> logs/fail/<time>-<kind>.txt"""
    os.makedirs(FAIL_DIR, exist_ok=True)
    name = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{kind}{'-' + ref if ref else ''}.txt"
    path = os.path.join(FAIL_DIR, name)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(text if isinstance(text, str) else json.dumps(text, indent=1, default=str))
    _prune(FAIL_DIR, keep=200)
    return os.path.relpath(path, config.ROOT)


def record(exc, component, **ctx):
    """Append one JSON line to logs/errors.jsonl and return it (the GUI and job.json show it)."""
    code = classify(exc, component)
    ctx = {**(getattr(exc, "ctx", {}) or {}), **{k: v for k, v in ctx.items() if v not in (None, "")}}
    entry = {
        "ref": new_ref(),
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "component": component,
        "code": code,
        "message": str(exc)[:1500],
        "hint": getattr(exc, "hint", "") or HINTS.get(code, ""),
        "ctx": ctx,
        "type": type(exc).__name__,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:],
    }
    try:
        os.makedirs(config.LOGS_DIR, exist_ok=True)
        with open(ERR_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        if os.path.getsize(ERR_LOG) > 5_000_000:      # keep the log readable: last ~1000 errors
            with open(ERR_LOG, encoding="utf-8") as f:
                lines = f.readlines()[-1000:]
            with open(ERR_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except OSError:
        pass
    return entry


def public(entry):
    """What the GUI / job.json show (no traceback)."""
    return {k: entry.get(k) for k in ("ref", "code", "message", "hint", "ctx", "component", "time")}


def quiet(component, exc, what=""):
    """An error we deliberately ignore (best-effort cleanup): one line in logs/debug.log, never raised."""
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {component} {what}: "
                    f"{type(exc).__name__}: {str(exc)[:300]}\n")
        if os.path.getsize(DEBUG_LOG) > 2_000_000:
            os.replace(DEBUG_LOG, DEBUG_LOG + ".old")
    except OSError:
        pass


def recent(n=10, ref=None):
    try:
        with open(ERR_LOG, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if ref and e.get("ref") != ref:
            continue
        out.append(e)
        if len(out) >= n:
            break
    return out


def one_line(e):
    c = e.get("ctx") or {}
    where = " ".join(f"{k}={c[k]}" for k in ("project", "stage", "shot", "scene", "node", "task", "path") if c.get(k))
    det = f" | detail: {c['detail_file']}" if c.get("detail_file") else ""
    return f"{e.get('time', '')[11:19]} {e.get('ref')} {e.get('code'):<14} {e.get('component')} {where} | " \
           f"{e.get('message', '')[:220]}{det}"


def _prune(folder, keep):
    try:
        files = sorted((os.path.join(folder, f) for f in os.listdir(folder)), key=os.path.getmtime)
        for f in files[:-keep]:
            os.remove(f)
    except OSError:
        pass

"""Paths, per-workstation settings and hardware detection (stdlib only).

settings.json lives next to factory.py and belongs to THIS workstation: copy the folder to
another PC and it detects that PC's GPU/RAM and tools on first start.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
PROJECTS_DIR = os.path.join(ROOT, "projects")
EXPORTS_DIR = os.path.join(ROOT, "exports")
MUSIC_DIR = os.path.join(ROOT, "music")
BRAND_DIR = os.path.join(ROOT, "brand")
SFX_DIR = os.path.join(ROOT, "sfx")
VOICES_DIR = os.path.join(ROOT, "voices")
LOGS_DIR = os.path.join(ROOT, "logs")
SETTINGS_FILE = os.path.join(ROOT, "settings.json")

for _d in (PROJECTS_DIR, EXPORTS_DIR, MUSIC_DIR, BRAND_DIR, SFX_DIR, VOICES_DIR, LOGS_DIR):
    os.makedirs(_d, exist_ok=True)

QUALITY = {
    # image = SDXL still, video = LTX/Wan clip size (portrait, divisible by 32)
    "draft":    {"img": (640, 1136), "img_steps": 18, "vid": (384, 672), "vid_steps": 6, "crf": 23},
    "standard": {"img": (768, 1344), "img_steps": 25, "vid": (512, 896), "vid_steps": 8, "crf": 20},
    "high":     {"img": (768, 1344), "img_steps": 30, "vid": (576, 1024), "vid_steps": 10, "crf": 18},
    "max":      {"img": (832, 1472), "img_steps": 32, "vid": (704, 1248), "vid_steps": 12, "crf": 17},
}

ENGINES = {
    "ltx_i2v": "Image + LTX animation (best balance)",
    "still": "Image + camera move (fastest, any GPU)",
    "ltx_t2v": "LTX text-to-video",
    "wan": "Wan 2.1 text-to-video (slow)",
    "own": "My own clip or picture",
}

DEFAULTS = {
    "comfy_url": "http://127.0.0.1:8188",
    "comfy_dir": "",            # local ComfyUI folder (models + auto start); empty = auto-detect
    "comfy_autostart": True,
    "ollama_url": "http://127.0.0.1:11434",
    "port": 8770,
    "vram_gb": 0,               # 0 = auto-detect
    "ram_gb": 0,
    "llm": "",                  # empty = best installed model that fits
    "llm_ctx": 8192,
    "image_model": "",          # SDXL checkpoint; empty = first one found
    "video_model": "",          # LTX checkpoint; empty = first one found
    "engine": "ltx_i2v",
    "quality": "",              # empty = picked from VRAM
    "voice": "female_01",
    "voice_language": "English",
    "voice_speed": 1.0,
    "voice_emotion": 0.5,
    "own_voice": "",            # file in voices/
    "music": "builtin",         # none | builtin | file | ace
    "music_file": "",           # file in music/ ("" = random)
    "music_volume": 0.22,
    "sfx": True,
    "sfx_engine": "builtin",    # builtin (ffmpeg + sfx/ folder) | ai (Stable Audio, if downloaded)
    "sfx_volume": 0.35,
    "subtitles": True,
    "onscreen_text": True,
    "sub_font": "",
    "transition": "crossfade",  # crossfade | fade (dip to black) | cut
    "look": "film",             # film (shared grade + fine grain) | clean
    "chapter_titles": True,     # chapter title on screen when a chapter starts
    # branding for marketing (off while brand_text and banner_text are empty)
    "brand_text": "",            # e.g. voomreel.com: watermark + end card
    "brand_watermark": True,     # small brand text in a corner for the whole video
    "brand_position": "top-left",  # top-left | top-center | top-right | bottom-left
    "brand_opacity": 0.8,        # 0.2-1.0
    "brand_logo": "",            # PNG/WebP file in brand/ (with the watermark and on the end card)
    "brand_color": "#FFB020",    # banner colour
    "banner_text": "",           # e.g. "Free video downloader -> voomreel.com" ("" = no banner)
    "banner_when": "start",      # start (first 5 s) | end (last 5 s before the end card) | always
    "end_card": True,            # 3 s closing card with the brand (needs brand_text)
    "end_card_line": "",         # small line above the brand on the end card
    "caption_add": "",           # added to every TikTok caption, e.g. "🔗 voomreel.com"
    "branding": "on",            # per project: on | off
    "hf_token": "",
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(new):
    s = load_settings()
    for k, v in new.items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
            elif isinstance(d, (int, float)):
                v = type(d)(float(v or 0))
            else:
                v = str(v).strip()
        except (TypeError, ValueError):
            continue
        s[k] = v
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)
    return s


def find_comfy_dir(s=None):
    s = s or load_settings()
    cands = [s.get("comfy_dir"), os.environ.get("COMFY_DIR"), "~/comfy/ComfyUI", "~/ComfyUI", "~/comfy",
             "~/ComfyUI_windows_portable/ComfyUI", "C:/ComfyUI_windows_portable/ComfyUI"]
    for c in cands:
        if c:
            p = os.path.expanduser(c)
            if os.path.isfile(os.path.join(p, "main.py")) and os.path.isdir(os.path.join(p, "models")):
                return p
    return ""


def _http_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def detect_hardware(s=None):
    """GPU memory and RAM of the machine that RENDERS (ComfyUI's machine if reachable)."""
    s = s or load_settings()
    hw = {"gpu": "", "vram_gb": 0.0, "ram_gb": 0.0, "source": "", "os": platform.system()}
    try:
        st = _http_json(s["comfy_url"].rstrip("/") + "/system_stats")
        dev = (st.get("devices") or [{}])[0]
        hw.update(gpu=dev.get("name", ""), vram_gb=round(dev.get("vram_total", 0) / 2**30, 1),
                  ram_gb=round(st.get("system", {}).get("ram_total", 0) / 2**30, 1), source="ComfyUI")
        hw["gpu"] = hw["gpu"].split(" : ")[0].replace("cuda:0 ", "").replace("xpu:0 ", "")
    except Exception:
        pass
    if not hw["vram_gb"] and shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
            name, mem = out.rsplit(",", 1)
            hw.update(gpu=name.strip(), vram_gb=round(float(mem) / 1024, 1), source="nvidia-smi")
        except Exception:
            pass
    if not hw["gpu"]:
        hw.update({k: v for k, v in _detect_intel_arc().items() if v})
    if not hw["ram_gb"]:
        hw["ram_gb"] = _local_ram_gb()
    return hw


def _detect_intel_arc():
    """Intel Arc GPU without ComfyUI running: xpu-smi, else the Windows device list.

    Name only in practice: on Arc laptops VRAM is shared RAM, so the real number
    comes from ComfyUI /system_stats once it runs, or from the Settings fields.
    """
    hw = {"gpu": "", "vram_gb": 0.0, "source": ""}
    if shutil.which("xpu-smi"):
        try:
            out = subprocess.run(["xpu-smi", "discovery"], capture_output=True, text=True, timeout=15).stdout
            m = re.search(r"Device Name:\s*(.+)", out)
            if m:
                hw.update(gpu=m.group(1).strip(), source="xpu-smi")
        except Exception:
            pass
    if not hw["gpu"] and platform.system() == "Windows":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
                                 capture_output=True, text=True, timeout=20).stdout
            name = next((l.strip() for l in out.splitlines() if "arc" in l.lower()), "")
            if name:
                hw.update(gpu=name, source="windows")
        except Exception:
            pass
    return hw


def _local_ram_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 2**20, 1)
    except OSError:
        pass
    if platform.system() == "Windows":
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2**30, 1)
        except Exception:
            pass
    if platform.system() == "Darwin":
        try:
            return round(int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])) / 2**30, 1)
        except Exception:
            pass
    return 0.0


def hardware(s=None):
    """Effective hardware: what the user typed wins, otherwise detection."""
    s = s or load_settings()
    hw = detect_hardware(s)
    eff = dict(hw)
    eff["detected_vram_gb"], eff["detected_ram_gb"] = hw["vram_gb"], hw["ram_gb"]
    if float(s.get("vram_gb") or 0) > 0:
        eff["vram_gb"] = float(s["vram_gb"])
    if float(s.get("ram_gb") or 0) > 0:
        eff["ram_gb"] = float(s["ram_gb"])
    return eff


def recommended(vram, ram):
    """Quality preset + engine that suit this GPU."""
    if vram < 6:
        q, eng = "draft", "still"
    elif vram < 10:
        q, eng = "standard", "ltx_i2v"
    elif vram < 16:
        q, eng = "high", "ltx_i2v"
    else:
        q, eng = "max", "ltx_i2v"
    if ram and ram < 12 and q in ("high", "max"):
        q = "standard"
    return {"quality": q, "engine": eng,
            "frames_cap": 121 if vram < 12 else (161 if vram < 20 else 241),
            "llm_max_gb": round(max(1.5, vram - 1.0), 1),
            "llm_cpu_max_gb": round(max(1.5, vram - 1.0 + max(0, ram - 6) * 0.6), 1)}


def effective_quality(s=None, hw=None):
    s = s or load_settings()
    if s.get("quality") in QUALITY:
        return s["quality"]
    hw = hw or hardware(s)
    return recommended(hw["vram_gb"], hw["ram_gb"])["quality"]


def fit(size_gb, need_vram, need_ram, vram, ram):
    """fits | tight | too_big for a ComfyUI model on this machine."""
    if not vram:
        return "unknown"
    if vram >= need_vram and (not ram or ram >= need_ram):
        return "fits"
    if vram >= need_vram * 0.7 and (not ram or ram >= need_ram * 0.8):
        return "tight"
    return "too_big"


def ffmpeg_ok():
    exe = shutil.which("ffmpeg")
    if not exe:
        return False, "ffmpeg not found"
    try:
        out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10).stdout
        if " ass " not in out:
            return False, "ffmpeg has no libass (subtitles)"
    except Exception as e:
        return False, str(e)
    return True, "ok"

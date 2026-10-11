"""Model manager: a short catalog of ComfyUI models (Hugging Face), fit against this machine's
GPU/RAM, resumable downloads, and LLM suggestions for Ollama.

Downloads only start when the user clicks Download in the GUI (or runs factory.py download).
"""
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import config
from errors import FactoryError

HF = "https://huggingface.co"

# id: (use, label, repo, path in repo, ComfyUI folder, save-as name, size GB, min VRAM, min RAM, note)
CATALOG = [
    ("image", "SDXL base 1.0", "stabilityai/stable-diffusion-xl-base-1.0", "sd_xl_base_1.0.safetensors",
     "checkpoints", "", 6.9, 6, 12, "Pictures for every scene. Needed for the 'image' engines."),
    ("image", "RealVisXL V5 (photo-real SDXL)", "SG161222/RealVisXL_V5.0", "RealVisXL_V5.0_fp16.safetensors",
     "checkpoints", "", 6.9, 6, 12, "More realistic people and places than SDXL base."),
    ("video", "LTX-Video 2B distilled", "Lightricks/LTX-Video", "ltxv-2b-0.9.8-distilled.safetensors",
     "checkpoints", "", 6.3, 8, 14, "Animates the pictures. Fast, good motion."),
    ("video", "LTX-Video 2B distilled fp8", "Lightricks/LTX-Video", "ltxv-2b-0.9.8-distilled-fp8.safetensors",
     "checkpoints", "", 4.5, 6, 12, "Smaller LTX for 6-8 GB cards."),
    ("video", "LTX-Video 13B distilled fp8", "Lightricks/LTX-Video", "ltxv-13b-0.9.8-distilled-fp8.safetensors",
     "checkpoints", "", 15.7, 16, 32, "Much better LTX for big cards."),
    ("video", "T5-XXL text encoder (for LTX)", "comfyanonymous/flux_text_encoders",
     "t5xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders", "", 5.2, 0, 12, "Required by every LTX model."),
    ("video", "Wan 2.1 text-to-video 1.3B", "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
     "split_files/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors", "diffusion_models", "", 2.8, 8, 16,
     "Alternative video engine (slower). Needs the two files below."),
    ("video", "Wan 2.1 text encoder (UMT5)", "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
     "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders", "", 6.7, 0, 16, "For Wan."),
    ("video", "Wan 2.1 VAE", "Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/vae/wan_2.1_vae.safetensors",
     "vae", "", 0.25, 0, 0, "For Wan."),
    ("music", "ACE-Step v1 3.5B (music)", "Comfy-Org/ACE-Step_ComfyUI_repackaged",
     "all_in_one/ace_step_v1_3.5b.safetensors", "checkpoints", "", 7.7, 8, 16,
     "Generates instrumental music from the MUSIC line."),
    ("sfx", "Stable Audio Open 1.0 (sound effects)", "Comfy-Org/stable-audio-open-1.0_repackaged",
     "stable-audio-open-1.0.safetensors", "checkpoints", "", 4.9, 6, 12,
     "Generates sound effects from the SOUND line. Needs the T5-base file below."),
    ("sfx", "T5-base text encoder (for Stable Audio)", "google-t5/t5-base", "model.safetensors",
     "text_encoders", "t5_base.safetensors", 0.9, 0, 0, "For Stable Audio."),
]

# Ollama models that write good stories. Sizes are about the download size (Q4).
LLM_SUGGESTIONS = [
    ("llama3.2:3b", 2.0, "Small and quick; OK stories, simple language."),
    ("qwen2.5:7b", 4.7, "Good all-rounder, follows the format well."),
    ("gemma3:12b", 8.1, "Better storytelling; fits 10-12 GB cards fully."),
    ("qwen2.5:14b", 9.0, "Strong writer for 12 GB+ cards."),
    ("mistral-small:24b", 14.0, "Very good writer, needs 16 GB+ VRAM (or lots of RAM, slower)."),
    ("gemma3:27b", 17.0, "Best quality here; 24 GB cards, or slow on CPU."),
]


def _id(repo, path):
    return (repo + "/" + path).replace("/", "__")


def catalog_entries():
    out = []
    for use, label, repo, path, folder, save_as, size, vram, ram, note in CATALOG:
        out.append({"id": _id(repo, path), "use": use, "label": label, "repo": repo, "path": path,
                    "folder": folder, "file": save_as or os.path.basename(path), "size_gb": size,
                    "min_vram": vram, "min_ram": ram, "note": note})
    return out


def installed_files(comfy_mod):
    """{folder: set(files)} from ComfyUI's API, else from the local ComfyUI folder."""
    have = {}
    for folder in {e["folder"] for e in catalog_entries()} | {"checkpoints", "diffusion_models", "text_encoders", "vae"}:
        files = comfy_mod.models(folder) if comfy_mod.alive() else []
        if not files:
            d = config.find_comfy_dir()
            p = os.path.join(d, "models", folder) if d else ""
            if p and os.path.isdir(p):
                files = [f for f in os.listdir(p) if not f.startswith("put_") and not f.endswith(".part")]
        have[folder] = set(files)
    return have


def catalog_status(comfy_mod, hw, tasks=None):
    have = installed_files(comfy_mod)
    out = []
    for e in catalog_entries():
        e = dict(e)
        e["installed"] = e["file"] in have.get(e["folder"], set())
        e["fit"] = config.fit(e["size_gb"], e["min_vram"], e["min_ram"], hw.get("vram_gb", 0), hw.get("ram_gb", 0))
        out.append(e)
    return out


def llm_fit(size_gb, vram, ram):
    if not vram:
        return "unknown"
    if size_gb + 1.0 <= vram:
        return "fits"                       # all on the GPU: fast
    if size_gb + 1.0 <= vram + max(0, ram - 6) * 0.7:
        return "tight"                      # part on the CPU: works, slower
    return "too_big"


def comfy_models_dir():
    d = config.find_comfy_dir()
    return os.path.join(d, "models") if d else ""


def download(url, dest, token="", on_progress=None, should_stop=None):
    """Stream a file with resume (Range) into dest; .part until complete."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": "Ole-K2-Free-Video/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:           # already complete
            os.replace(part, dest)
            return dest
        if e.code in (401, 403):
            raise FactoryError("DOWNLOAD", "This model needs a Hugging Face login: accept its license on huggingface.co "
                               "and put your token in Settings.") from None
        raise
    with resp:
        if have and resp.status != 206:
            have = 0                # server ignored Range: start over
        total = have + int(resp.headers.get("Content-Length") or 0)
        mode = "ab" if have else "wb"
        done, t0, last = have, time.time(), 0.0
        with open(part, mode) as f:
            while True:
                if should_stop and should_stop():
                    raise FactoryError("CANCELLED", "cancelled (the part file is kept, Download resumes)")
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if on_progress and now - last > 0.5:
                    last = now
                    on_progress(done, total, (done - have) / max(0.1, now - t0))
    if total and os.path.getsize(part) < total:
        raise FactoryError("DOWNLOAD", "download stopped early (click Download again to resume)")
    os.replace(part, dest)
    return dest


def download_entry(entry, token="", on_progress=None, should_stop=None):
    base = comfy_models_dir()
    if not base:
        raise FactoryError("MODEL_MISSING", "No local ComfyUI folder found. Set it in Settings (models go into its models/ folder). "
                           "If ComfyUI runs on another PC, download the model there.")
    url = f"{HF}/{entry['repo']}/resolve/main/{urllib.parse.quote(entry['path'])}"
    dest = os.path.join(base, entry["folder"], entry["file"])
    if os.path.exists(dest):
        return dest
    want = expected_sha256(entry["repo"], entry["path"], token)
    download(url, dest + ".dl", token, on_progress, should_stop)
    if want:
        if on_progress:
            on_progress(-1, 0, 0)               # GUI shows "verifying"
        got = sha256_file(dest + ".dl", should_stop)
        if got != want:
            os.remove(dest + ".dl")
            raise FactoryError("DOWNLOAD", "Checksum mismatch: the download was damaged and was removed. Try again.")
    os.replace(dest + ".dl", dest)
    return dest


def expected_sha256(repo, path, token=""):
    """SHA-256 Hugging Face publishes for an LFS file ('' if unknown)."""
    import json
    folder = os.path.dirname(path)
    url = f"{HF}/api/models/{repo}/tree/main" + ("/" + urllib.parse.quote(folder) if folder else "")
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            for it in json.loads(r.read()):
                if it.get("path") == path:
                    return ((it.get("lfs") or {}).get("oid") or "").lower()
    except Exception:
        pass
    return ""


def sha256_file(path, should_stop=None):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if should_stop and should_stop():
                raise FactoryError("CANCELLED", "cancelled")
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def custom_entry(url, folder, filename=""):
    """Any Hugging Face file link (…/blob/main/… or …/resolve/main/…) -> a catalog-like entry."""
    u = url.strip()
    if not u.startswith(HF + "/"):
        raise FactoryError("BAD_REQUEST", "Paste a https://huggingface.co/... file link")
    u = u.replace("/blob/", "/resolve/").split("?")[0]
    parts = u[len(HF) + 1:].split("/")
    if "resolve" not in parts:
        raise FactoryError("BAD_REQUEST", "The link must point to one file (…/resolve/main/<file>)")
    i = parts.index("resolve")
    repo, path = "/".join(parts[:i]), "/".join(parts[i + 2:])
    allowed = {"checkpoints", "diffusion_models", "text_encoders", "vae", "loras", "upscale_models", "clip_vision"}
    if folder not in allowed:
        raise FactoryError("BAD_REQUEST", "Pick a model folder")
    return {"id": _id(repo, path), "repo": repo, "path": urllib.parse.unquote(path), "folder": folder,
            "file": filename.strip() or os.path.basename(urllib.parse.unquote(path)), "label": os.path.basename(path)}


def hf_search_gguf(query, limit=15):
    """Hugging Face repos with GGUF files (for Ollama: hf.co/<repo>:<quant>)."""
    import json
    q = urllib.parse.urlencode({"search": query, "filter": "gguf", "sort": "downloads", "limit": limit})
    with urllib.request.urlopen(f"{HF}/api/models?{q}", timeout=20) as r:
        return [{"repo": m["id"], "downloads": m.get("downloads", 0), "likes": m.get("likes", 0)}
                for m in json.loads(r.read())]


def hf_gguf_files(repo, vram, ram):
    import json
    with urllib.request.urlopen(f"{HF}/api/models/{repo}/tree/main", timeout=20) as r:
        items = json.loads(r.read())
    out = []
    for it in items:
        p = it.get("path", "")
        if p.lower().endswith(".gguf") and "mmproj" not in p.lower():
            size = (it.get("lfs") or {}).get("size") or it.get("size", 0)
            gb = round(size / 1e9, 1)
            stem = p[:-5]
            quant = stem.rsplit("-", 1)[-1] if "-" in stem else stem.rsplit(".", 1)[-1]
            out.append({"file": p, "size_gb": gb, "quant": quant, "ollama": f"hf.co/{repo}:{quant}",
                        "fit": llm_fit(gb, vram, ram)})
    return sorted(out, key=lambda x: x["size_gb"])

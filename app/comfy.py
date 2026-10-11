"""Tiny ComfyUI client (stdlib only) + the workflows the factory uses.

Talks to ComfyUI only over its HTTP API (upload, queue, /view download), so ComfyUI may run
on this PC or on another workstation (settings: comfy_url). Every generator saves its result
to `dest` and returns that path. The factory never downloads models by itself.
"""
import json
import os
import re
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import errors
from errors import FactoryError

URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")

NEG_IMAGE = ("low quality, blurry, distorted, watermark, text, letters, caption, logo, signature, "
             "deformed, extra fingers, bad anatomy, jpeg artifacts, worst quality")
NEG_VIDEO = ("low quality, blurry, distorted, watermark, text, letters, logo, deformed, "
             "worst quality, inconsistent motion, jittery, flicker")

# Voices offered: generic sample voices only. The node pack also ships clones of real people
# (celebrities and named higgs voices); those are never offered (impersonation on TikTok).
_VOICE_OK = re.compile(r"voices_examples/(female|male)/[^/]+\.wav$|voices_examples/higgs_audio/en_(man|woman)\.wav$")
FALLBACK_VOICES = {
    "female_01": "voices_examples/female/female_01.wav",
    "female_02": "voices_examples/female/female_02.wav",
    "male_01": "voices_examples/male/male_01.wav",
    "male_02": "voices_examples/male/male_02.wav",
    "en_woman": "voices_examples/higgs_audio/en_woman.wav",
    "en_man": "voices_examples/higgs_audio/en_man.wav",
}


def configure(url):
    global URL
    URL = (url or URL).rstrip("/")


def _req(path, data=None, timeout=30, method=None, raw=False):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(URL + path, data=body, method=method,
                               headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            out = resp.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise FactoryError("COMFY_DOWN", f"ComfyUI not reachable at {URL} ({getattr(e, 'reason', e)})",
                           url=URL + path) from None
    if raw:
        return out
    return json.loads(out) if out else {}


def alive():
    try:
        _req("/system_stats", timeout=3)
        return True
    except Exception:
        return False


def free_vram():
    """Unload ComfyUI's models so Ollama gets the GPU (small cards cannot hold both)."""
    try:
        _req("/free", {"unload_models": True, "free_memory": True}, timeout=10)
    except Exception as e:
        errors.quiet("comfy", e, "free_vram")


def interrupt():
    try:
        _req("/interrupt", {}, timeout=5)
    except Exception as e:
        errors.quiet("comfy", e, "interrupt")


_OI = {}


def object_info(node):
    if node not in _OI:
        try:
            _OI[node] = _req("/object_info/" + urllib.parse.quote(node), timeout=20).get(node)
        except Exception:
            _OI[node] = None
    return _OI[node]


def has_node(node):
    return object_info(node) is not None


def models(folder):
    """Model files ComfyUI sees in one folder (checkpoints, diffusion_models, ...)."""
    try:
        return _req("/models/" + folder, timeout=10)
    except Exception:
        return []


def voices():
    """{short name: path} for the generic voices this ComfyUI offers."""
    info = object_info("UnifiedTTSTextNode")
    try:
        opts = info["input"]["required"]["narrator_voice"][0]
    except (TypeError, KeyError, IndexError):
        return {}
    out = {}
    for p in opts:
        if isinstance(p, str) and _VOICE_OK.search(p):
            out[os.path.splitext(os.path.basename(p))[0]] = p
    return out or {}


def tts_languages():
    info = object_info("ChatterBoxEngineNode")
    try:
        return [x for x in info["input"]["required"]["language"][0] if not x.startswith("local:")]
    except (TypeError, KeyError, IndexError):
        return ["English"]


def upload(path):
    """Send a local file to ComfyUI's input folder; returns the name nodes refer to."""
    name = f"rf_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}".replace(" ", "_")
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
            f"\r\n--{boundary}--\r\n").encode()
    r = urllib.request.Request(URL + "/upload/image", data=body,
                               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(r, timeout=120) as resp:
        res = json.loads(resp.read())
    return (res.get("subfolder") + "/" if res.get("subfolder") else "") + res.get("name", name)


def download(item, dest):
    q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                "type": item.get("type", "output")})
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with urllib.request.urlopen(URL + "/view?" + q, timeout=300) as resp, open(dest + ".part", "wb") as f:
        shutil.copyfileobj(resp, f)
    os.replace(dest + ".part", dest)
    return dest


def _workflow_summary(workflow):
    return ", ".join(sorted({n.get("class_type", "?") for n in workflow.values()}))


def _refused(e, workflow):
    """ComfyUI said 400 to /prompt: name the node and the input it did not like."""
    raw = e.read().decode(errors="replace")
    try:
        js = json.loads(raw)
    except ValueError:
        js = {}
    parts, code = [], "COMFY_REFUSED"
    top = js.get("error") or {}
    if top.get("type") in ("missing_node_type", "invalid_prompt") and "node" in str(top.get("message", "")).lower():
        code = "NODE_MISSING"
    for nid, ne in (js.get("node_errors") or {}).items():
        for er in ne.get("errors", []):
            inp = (er.get("extra_info") or {}).get("input_name", "")
            got = (er.get("extra_info") or {}).get("received_value", "")
            parts.append(f"{ne.get('class_type', '?')}.{inp}: {er.get('message', '')} {er.get('details', '')}".strip()
                         + (f" (got {got!r})" if got != "" else ""))
            if er.get("type") == "value_not_in_list" and inp.endswith("_name"):
                code = "MODEL_MISSING"
    msg = "; ".join(parts) or str(top.get("message") or raw[:300])
    detail = errors.save_detail("comfy-refused", {"response": js or raw[:4000], "workflow": workflow})
    raise FactoryError(code, f"ComfyUI refused the job: {msg[:600]}", detail_file=detail,
                       nodes=_workflow_summary(workflow)) from None


def run(workflow, timeout=900, on_tick=None):
    """Queue a workflow, wait for it, return the list of output items. Failures name the node and keep
    the workflow + ComfyUI's traceback in logs/fail/ (detail_file)."""
    try:
        pid = _req("/prompt", {"prompt": workflow, "client_id": uuid.uuid4().hex})["prompt_id"]
    except urllib.error.HTTPError as e:
        _refused(e, workflow)
    t0 = time.time()
    while True:
        h = _req(f"/history/{pid}", timeout=10).get(pid)
        if h:
            st = h.get("status", {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                err = msgs[0][1] if msgs else {}
                node = err.get("node_type", "?")
                text = str(err.get("exception_message", "unknown")).strip()
                detail = errors.save_detail("comfy-node", {
                    "node_type": node, "node_id": err.get("node_id"), "exception_type": err.get("exception_type"),
                    "exception_message": text, "traceback": err.get("traceback"), "workflow": workflow})
                code = "GPU_OOM" if re.search(r"out of memory|OutOfMemory|Allocation on device", text, re.I) else \
                    ("MODEL_MISSING" if re.search(r"No such file|not found|does not exist", text, re.I) else "COMFY_NODE")
                raise FactoryError(code, f"ComfyUI node {node} failed: {text[:400]}", node=node, detail_file=detail)
            if st.get("completed") or h.get("outputs"):
                items = []
                for out in h.get("outputs", {}).values():
                    for vals in out.values():
                        if isinstance(vals, list):
                            items += [it for it in vals if isinstance(it, dict) and it.get("filename")
                                      and it.get("type") == "output"]
                if items:
                    return items
                detail = errors.save_detail("comfy-no-output", {"history": h, "workflow": workflow})
                raise FactoryError("COMFY_NODE", "ComfyUI finished without an output file", detail_file=detail)
        if time.time() - t0 > timeout:
            interrupt()
            raise FactoryError("TIMEOUT", f"ComfyUI took longer than {timeout}s (stopped it)",
                               nodes=_workflow_summary(workflow))
        if on_tick:
            on_tick(time.time() - t0)
        time.sleep(1.0)


def _run_to(workflow, dest, timeout, on_tick):
    return download(run(workflow, timeout, on_tick)[0], dest)


# ------------------------------------------------------------------ workflows
def sdxl_image(prompt, dest, ckpt, seed=1, width=768, height=1344, steps=25, negative=NEG_IMAGE,
               timeout=600, on_tick=None):
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": steps, "cfg": 7.0, "sampler_name": "dpmpp_2m",
            "scheduler": "karras", "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "factory/img"}},
    }
    return _run_to(wf, dest, timeout, on_tick)


def _ltx_tail(wf, cond_pos, cond_neg, latent, fps, seed, steps):
    wf.update({
        "20": {"class_type": "LTXVConditioning", "inputs": {"positive": cond_pos, "negative": cond_neg, "frame_rate": fps}},
        "21": {"class_type": "ModelSamplingLTXV", "inputs": {"model": ["1", 0], "max_shift": 2.05, "base_shift": 0.95,
                                                            "latent": latent}},
        "22": {"class_type": "KSampler", "inputs": {
            "model": ["21", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["20", 0], "negative": ["20", 1], "latent_image": latent,
            "denoise": 1.0}},
        "23": {"class_type": "VAEDecode", "inputs": {"samples": ["22", 0], "vae": ["1", 2]}},
        "24": {"class_type": "CreateVideo", "inputs": {"images": ["23", 0], "fps": float(fps)}},
        "25": {"class_type": "SaveVideo", "inputs": {"video": ["24", 0], "filename_prefix": "factory/vid",
                                                     "format": "mp4", "codec": "h264"}},
    })
    return wf


def _ltx_head(ckpt, prompt, negative):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
    }


def ltx_frames(seconds, fps, cap=121):
    """LTX wants 8n+1 frames."""
    n = max(2, round(seconds * fps / 8))
    return min(cap, n * 8 + 1)


def ltx_video(prompt, dest, ckpt, seconds=4.0, seed=1, width=512, height=896, fps=24, steps=8, cap=121,
              negative=NEG_VIDEO, timeout=900, on_tick=None):
    wf = _ltx_head(ckpt, prompt, negative)
    wf["5"] = {"class_type": "EmptyLTXVLatentVideo", "inputs": {
        "width": width, "height": height, "length": ltx_frames(seconds, fps, cap), "batch_size": 1}}
    _ltx_tail(wf, ["3", 0], ["4", 0], ["5", 0], fps, seed, steps)
    return _run_to(wf, dest, timeout, on_tick)


def ltx_animate(image_path, prompt, dest, ckpt, seconds=4.0, seed=1, width=512, height=896, fps=24, steps=8,
                cap=121, strength=1.0, negative=NEG_VIDEO, timeout=900, on_tick=None):
    """Image-to-video: the still is the first frame, LTX adds the motion."""
    wf = _ltx_head(ckpt, prompt, negative)
    wf["6"] = {"class_type": "LoadImage", "inputs": {"image": upload(image_path)}}
    wf["7"] = {"class_type": "LTXVImgToVideo", "inputs": {
        "positive": ["3", 0], "negative": ["4", 0], "vae": ["1", 2], "image": ["6", 0], "width": width,
        "height": height, "length": ltx_frames(seconds, fps, cap), "batch_size": 1, "strength": strength}}
    _ltx_tail(wf, ["7", 0], ["7", 1], ["7", 2], fps, seed, steps)
    return _run_to(wf, dest, timeout, on_tick)


def wan_video(prompt, dest, seconds=5.0, seed=1, width=480, height=832, fps=16, steps=25,
              negative=NEG_VIDEO, timeout=1500, on_tick=None):
    frames = min(81, max(17, round(seconds * fps / 4) * 4 + 1))   # Wan wants 4n+1
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.1_t2v_1.3B_fp16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "6": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": width, "height": height, "length": frames, "batch_size": 1}},
        "7": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["7", 0], "seed": seed, "steps": steps, "cfg": 6.0, "sampler_name": "uni_pc",
            "scheduler": "simple", "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": float(fps)}},
        "11": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": "factory/wan", "format": "mp4", "codec": "h264"}},
    }
    return _run_to(wf, dest, timeout, on_tick)


def tts(text, dest, voice_path, own_voice_path=None, exaggeration=0.5, temperature=0.8,
        cfg_weight=0.5, seed=1, language="English", timeout=600, on_tick=None):
    """Chatterbox narration. own_voice_path: a WAV of the user's own voice (cloned)."""
    wf = {
        "1": {"class_type": "ChatterBoxEngineNode", "inputs": {
            "language": language, "device": "auto", "exaggeration": exaggeration, "temperature": temperature,
            "cfg_weight": cfg_weight, "crash_protection_template": "hmm ,, {seg} hmm ,,"}},
        "2": {"class_type": "UnifiedTTSTextNode", "inputs": {
            "TTS_engine": ["1", 0], "text": text, "narrator_voice": voice_path or FALLBACK_VOICES["female_01"],
            "seed": seed, "enable_chunking": True, "max_chars_per_chunk": 400,
            "chunk_combination_method": "auto", "silence_between_chunks_ms": 100,
            "enable_audio_cache": True, "batch_size": 0}},
        "3": {"class_type": "SaveAudio", "inputs": {"audio": ["2", 0], "filename_prefix": "factory/voice"}},
    }
    if own_voice_path:
        wf["4"] = {"class_type": "LoadAudio", "inputs": {"audio": upload(own_voice_path)}}
        wf["5"] = {"class_type": "CharacterVoicesNode", "inputs": {
            "voice_name": "none", "reference_text": "", "trim_start": 0.0, "trim_end": 0.0,
            "customized": False, "opt_audio_input": ["4", 0]}}
        wf["2"]["inputs"]["narrator_voice"] = "none"
        wf["2"]["inputs"]["opt_narrator"] = ["5", 0]
    return _run_to(wf, dest, timeout, on_tick)


def ace_music(tags, dest, ckpt, seconds=30.0, seed=1, steps=50, timeout=900, on_tick=None):
    """Instrumental music with ACE-Step v1 (all-in-one checkpoint). Untested until downloaded."""
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "TextEncodeAceStepAudio", "inputs": {
            "clip": ["1", 1], "tags": tags, "lyrics": "[instrumental]", "lyrics_strength": 0.99}},
        "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
        "4": {"class_type": "EmptyAceStepLatentAudio", "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 5.0}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ["5", 0], "seed": seed, "steps": steps, "cfg": 5.0, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            "denoise": 1.0}},
        "7": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveAudio", "inputs": {"audio": ["7", 0], "filename_prefix": "factory/music"}},
    }
    return _run_to(wf, dest, timeout, on_tick)


def stable_audio_sfx(prompt, dest, seconds=4.0, seed=1, steps=50, timeout=600, on_tick=None):
    """Sound effect with Stable Audio Open 1.0 + t5-base. Untested until downloaded."""
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "stable-audio-open-1.0.safetensors"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5_base.safetensors", "type": "stable_audio"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, noise, music, speech", "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningStableAudio", "inputs": {
            "positive": ["3", 0], "negative": ["4", 0], "seconds_start": 0.0, "seconds_total": float(seconds)}},
        "6": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": steps, "cfg": 6.0, "sampler_name": "dpmpp_3m_sde_gpu",
            "scheduler": "exponential", "positive": ["5", 0], "negative": ["5", 1], "latent_image": ["6", 0],
            "denoise": 1.0}},
        "8": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveAudio", "inputs": {"audio": ["8", 0], "filename_prefix": "factory/sfx"}},
    }
    return _run_to(wf, dest, timeout, on_tick)

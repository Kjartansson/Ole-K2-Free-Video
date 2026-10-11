"""Render a project: script -> voices -> pictures -> animation -> clips -> subtitles + sound
-> final.mp4 + cover.jpg + caption.txt (+ a copy in exports/ ready for TikTok).

Runs as its own process (the GUI starts it), one render at a time, progress in job.json:
    python3 app/pipeline.py render <slug> [--scene N]
    python3 app/pipeline.py make story.txt [--engine still] [--quality draft]
Everything is cached in work/: re-rendering only redoes what changed.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import random
import re
import shutil
import signal
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy      # noqa: E402
import config     # noqa: E402
import errors     # noqa: E402
import llm        # noqa: E402
import media      # noqa: E402
import projects   # noqa: E402
import scriptfmt  # noqa: E402
from errors import FactoryError  # noqa: E402

LEAD, TAIL = 0.15, 0.35        # silence before / after each voice line
STAGES = [("voice", 15), ("image", 25), ("video", 35), ("clip", 10), ("sound", 5), ("final", 10)]
NON_IMAGE_CKPT = ("ltx", "ace_step", "stable-audio", "stable_audio", "wan", "svd", "hunyuan", "flux")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, slug):
        self.slug = slug
        self.file = projects.path(slug, "job.json")
        self.state = {"state": "running", "stage": "starting", "pct": 0, "message": "Starting", "pid": os.getpid(),
                      "started": time.time(), "updated": time.time(), "warnings": []}
        self.weights = dict(STAGES)
        self.done_weight = 0
        self.where = {"stage": "starting"}      # project/stage/scene/shot for error reports
        self.write()

    def at(self, sh=None, **kw):
        self.where = {"stage": self.state.get("stage")}
        if sh:
            self.where.update(scene=sh.get("scene"), shot=sh.get("id"))
        self.where.update(kw)

    def write(self):
        self.state["updated"] = time.time()
        with open(self.file + ".tmp", "w") as f:
            json.dump(self.state, f)
        os.replace(self.file + ".tmp", self.file)

    def stage(self, name, message):
        self.where = {"stage": name}
        if self.state["stage"] in self.weights and self.state["stage"] != name:
            self.done_weight += self.weights[self.state["stage"]]
        self.state.update(stage=name, message=message, pct=round(self.done_weight))
        self.write()

    def step(self, i, n, message):
        w = self.weights.get(self.state["stage"], 0)
        self.state.update(message=message, pct=round(self.done_weight + w * i / max(1, n)))
        self.write()

    def warn(self, text, exc=None):
        self.state["warnings"].append(text)
        log(f"WARNING: {text}")
        if exc is not None:      # a handled failure (fallback used): still on record for whoever debugs
            e = errors.record(exc, "render", level="warning", project=self.slug, **self.where)
            log(f"  (recorded as {e['ref']} {e['code']})")
        self.write()

    def finish(self, state, **kw):
        self.state.update(state=state, pct=100 if state == "done" else self.state["pct"], **kw)
        self.write()


LOGF = None


def log(msg):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    if LOGF:
        LOGF.write(line + "\n")
        LOGF.flush()
    print(line, flush=True)


def _key(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Cache:
    def __init__(self, work):
        self.file = os.path.join(work, "cache.json")
        try:
            with open(self.file) as f:
                self.d = json.load(f)
        except (OSError, ValueError):
            self.d = {}

    def fresh(self, path, key):
        return os.path.exists(path) and self.d.get(os.path.basename(path)) == key

    def put(self, path, key):
        self.d[os.path.basename(path)] = key
        with open(self.file, "w") as f:
            json.dump(self.d, f, indent=1)


def file_sig(p):
    try:
        st = os.stat(p)
        return f"{st.st_size}-{int(st.st_mtime)}"
    except OSError:
        return ""


# ------------------------------------------------------------------ options
def resolve_options(meta, settings):
    o = dict(settings)
    for k, v in (meta.get("options") or {}).items():
        if v not in ("", None):
            o[k] = v
    return o


def first_model(folder, wanted, accept):
    files = comfy.models(folder)
    if wanted and wanted in files:
        return wanted
    for f in files:
        if accept(f.lower()):
            return f
    return ""


def voice_lookup(name, comfy_voices):
    """-> (comfy voice path, own recording path)"""
    name = (name or "").strip()
    if not name:
        return None, None
    if name in comfy_voices:
        return comfy_voices[name], None
    for ext in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
        p = os.path.join(config.VOICES_DIR, name + ext)
        if os.path.exists(p):
            return None, p
    if os.path.exists(os.path.join(config.VOICES_DIR, name)):
        return None, os.path.join(config.VOICES_DIR, name)
    return None, None


# ------------------------------------------------------------------ shot planning
FRAMINGS = ["close-up shot", "wide shot", "medium shot from a different angle", "detail shot", "over-the-shoulder shot"]
OVERLAP = 0.35          # crossfade length between shots


def split_line(text, max_words):
    """Voice line -> pieces of at most max_words, cut at sentence, then clause, then word breaks."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    pieces = []
    for sent in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if len(sent.split()) <= max_words:
            pieces.append(sent)
            continue
        for cl in re.split(r"(?<=[,;:—–])\s+", sent):
            w = cl.split()
            while len(w) > max_words:
                k = math.ceil(len(w) / math.ceil(len(w) / max_words))
                pieces.append(" ".join(w[:k]))
                w = w[k:]
            if w:
                pieces.append(" ".join(w))
    lines = []
    for p in pieces:
        if lines and len(lines[-1].split()) + len(p.split()) <= max_words:
            lines[-1] += " " + p
        else:
            lines.append(p)
    if len(lines) > 1 and len(lines[-1].split()) < 4 and len(lines[-2].split()) + len(lines[-1].split()) <= max_words + 2:
        lines[-2] += " " + lines.pop()
    return lines


def limits(hw):
    """What one generated clip can hold on this machine -> how long a shot may be."""
    cap = config.recommended(hw["vram_gb"], hw["ram_gb"])["frames_cap"]
    cap_s = cap / 24
    max_words = int(max(8, min(20, round((cap_s * 1.25 - LEAD - TAIL) * scriptfmt.WORDS_PER_SEC))))
    return cap, cap_s, max_words


def plan_shots(sc, max_words, max_still, keep_whole=()):
    """Scenes -> shots that fit one clip each. Extra shots of a scene get another framing and seed,
    so a long line plays like normal film coverage (wide, close-up, detail) instead of a frozen frame."""
    shots = []
    for i, s in enumerate(sc["scenes"], 1):
        if scriptfmt.is_card(s):
            sh = dict(s)
            sh.update(scene=i, part=0, parts=1, id=f"s{i:02d}", card=True, seconds=s["seconds"] or 4.0)
            shots.append(sh)
            continue
        if i in keep_whole:
            lines = [s["voice"]]
        elif s["voice"]:
            lines = split_line(s["voice"], max_words)
        else:
            lines = [""] * max(1, math.ceil((s["seconds"] or 3.0) / max_still - 0.15))
        total_words = max(1, len(s["voice"].split()))
        for j, line in enumerate(lines):
            sh = dict(s)
            sh.update(scene=i, part=j, parts=len(lines), voice=line,
                      id=f"s{i:02d}" + ("" if j == 0 else chr(97 + j) if j < 26 else f"_{j + 1:03d}"))
            if s["seconds"]:
                sh["seconds"] = round(s["seconds"] * (len(line.split()) / total_words if s["voice"] else 1 / len(lines)), 2)
            if j:
                sh["text"] = ""
                sh["frame"] = FRAMINGS[(j - 1) % len(FRAMINGS)]
            shots.append(sh)
    return shots


def estimate_minutes(sc, o, hw):
    """Rough render time for the GUI (8 GB card measured: voice ~4 s, SDXL ~13 s, LTX ~15 s per clip)."""
    cap, cap_s, max_words = limits(hw)
    shots = plan_shots(sc, max_words, cap_s)
    q = config.QUALITY[o["quality"] if o.get("quality") in config.QUALITY else config.recommended(hw["vram_gb"], hw["ram_gb"])["quality"]]
    speed = 1.0 if hw["vram_gb"] < 10 else (0.65 if hw["vram_gb"] < 16 else 0.45)
    if hw["vram_gb"] and hw["vram_gb"] < 7:
        speed = 1.6
    img = q["img_steps"] / 25 * (q["img"][0] * q["img"][1]) / (768 * 1344) * 13
    vid = q["vid_steps"] / 8 * (q["vid"][0] * q["vid"][1]) / (512 * 896) * 15
    eng = o.get("engine", "ltx_i2v")
    per = 4 + (img if eng in ("still", "ltx_i2v") else 0) + {"ltx_i2v": vid, "ltx_t2v": vid, "wan": 120}.get(eng, 0) + 3
    long_extra = sum(max(0, math.ceil((sh.get("seconds") or 0) / (cap_s * 1.3)) - 1) for sh in shots if not sh.get("card"))
    shots_cost = sum(3 if sh.get("card") else per for sh in shots) + long_extra * vid
    secs = sum(scriptfmt.estimate(s) for s in sc["scenes"])
    total = 60 + shots_cost * speed + secs * 0.6
    return {"minutes": round(total / 60, 1), "shots": len(shots), "scenes": len(sc["scenes"]), "seconds": round(secs)}


# ------------------------------------------------------------------ render
def render(slug, only_scene=None):
    global LOGF
    pdir = projects.path(slug)
    work = os.path.join(pdir, "work")
    os.makedirs(work, exist_ok=True)
    LOGF = open(os.path.join(pdir, "render.log"), "a", encoding="utf-8")
    media.LOG = LOGF
    job = Job(slug)

    def on_term(*_):
        raise Cancelled()
    signal.signal(signal.SIGTERM, on_term)
    try:
        out = _render(slug, pdir, work, job, only_scene)
        job.finish("done", message="Finished", **out)
        log(f"DONE {out}")
        return out
    except Cancelled:
        comfy.interrupt()
        job.finish("cancelled", message="Cancelled")
        log("CANCELLED")
    except FactoryError as e:
        if e.code == "CANCELLED":
            job.finish("cancelled", message="Cancelled")
            log("CANCELLED")
            return
        _fail(job, e)
    except Exception as e:
        log(traceback.format_exc())
        _fail(job, e)
    finally:
        LOGF.close()


def _fail(job, e):
    """Record the error (logs/errors.jsonl), show code + hint + ref in the GUI, exit non-zero."""
    entry = errors.record(e, "render", project=job.slug, **job.where)
    log(f"ERROR [{entry['code']}] ref {entry['ref']} at {job.where}: {e}")
    if entry["ctx"].get("detail_file"):
        log(f"  detail: {entry['ctx']['detail_file']}")
    log(f"  hint: {entry['hint']}")
    job.finish("error", message=str(e)[:500], error=str(e)[:500], code=entry["code"], hint=entry["hint"],
               ref=entry["ref"], where=job.where, detail_file=entry["ctx"].get("detail_file", ""))
    raise SystemExit(1)


def _gpu(job, fn, *args, **kw):
    """Run one ComfyUI job; retry once after freeing the GPU (out of memory) or after ComfyUI is back
    (connection dropped). Anything else is raised with the job's location attached."""
    for attempt in (1, 2):
        try:
            return fn(*args, **kw)
        except FactoryError as e:
            if attempt == 2 or e.code not in ("GPU_OOM", "COMFY_DOWN"):
                raise
            job.warn(f"{e.code} at {job.where.get('shot') or job.where.get('stage')}: retrying once", e)
            if e.code == "GPU_OOM":
                comfy.free_vram()
                llm.unload_all()
                time.sleep(3)
            else:
                for _ in range(15):
                    if comfy.alive():
                        break
                    time.sleep(2)


def _render(slug, pdir, work, job, only_scene):
    settings = config.load_settings()
    meta = projects.load_meta(slug)
    o = resolve_options(meta, settings)
    sc = scriptfmt.parse(projects.load_script(slug))
    if not sc["scenes"]:
        raise FactoryError("SCRIPT", "The script has no scenes. Check it on the Story tab.")
    comfy.configure(o["comfy_url"])
    llm.configure(o["ollama_url"])
    cache = Cache(work)
    hw = config.hardware(settings)
    qname = o["quality"] if o.get("quality") in config.QUALITY else config.recommended(hw["vram_gb"], hw["ram_gb"])["quality"]
    q = config.QUALITY[qname]
    cap, cap_s, max_words = limits(hw)
    n_scenes = len(sc["scenes"])
    if only_scene and not 1 <= only_scene <= n_scenes:
        raise FactoryError("SCRIPT", f"There is no scene {only_scene}")
    sopt = lambda i: meta["scenes"].get(str(i)) or {}          # noqa: E731

    def engine_of(i):
        e = "own" if sopt(i).get("media") else (sopt(i).get("engine") or o["engine"])
        return e if e in config.ENGINES else "ltx_i2v"

    shots = plan_shots(sc, max_words, cap_s, keep_whole={i for i in range(1, n_scenes + 1) if engine_of(i) == "own"})
    for sh in shots:
        sh["engine"] = "card" if sh.get("card") else engine_of(sh["scene"])
        sh["seed"] = int(sopt(sh["scene"]).get("seed") or (int(meta.get("seed", 1)) + sh["scene"] * 101)) + sh["part"] * 7919
    todo = [sh for sh in shots if not only_scene or sh["scene"] == only_scene]
    n = len(shots)
    crossfade = o.get("transition") == "crossfade" and not only_scene
    log(f"=== render {slug} scenes={n_scenes} shots={n} (max {max_words} words/shot) "
        f"{'scene ' + str(only_scene) if only_scene else 'all'} quality={qname} engine={o['engine']} "
        f"gpu={hw.get('gpu')} {hw['vram_gb']}GB")

    engines = {sh["engine"] for sh in todo}
    needs_comfy = bool(engines - {"own", "card"}) or any(sh["voice"] for sh in todo)
    if needs_comfy:
        if not comfy.alive():
            raise FactoryError("COMFY_DOWN", f"ComfyUI is not running at {o['comfy_url']}. Start the factory with factory.sh "
                               "(it starts ComfyUI) or fix the address in Settings.")
        llm.unload_all()      # hand the GPU to ComfyUI
    img_ckpt = vid_ckpt = ""
    if engines & {"still", "ltx_i2v"}:
        img_ckpt = first_model("checkpoints", o.get("image_model"), lambda f: not any(x in f for x in NON_IMAGE_CKPT))
        if not img_ckpt:
            raise FactoryError("MODEL_MISSING", "No image model (SDXL) found. Download 'SDXL base 1.0' on the Models tab.")
    if engines & {"ltx_i2v", "ltx_t2v"}:
        vid_ckpt = first_model("checkpoints", o.get("video_model"), lambda f: "ltx" in f and "upscal" not in f)
        if not vid_ckpt or "t5xxl_fp8_e4m3fn_scaled.safetensors" not in comfy.models("text_encoders"):
            raise FactoryError("MODEL_MISSING", "LTX video model or its T5 text encoder is missing. Download both on the Models tab, "
                               "or pick the 'Image + camera move' engine.")
    if "wan" in engines and "wan2.1_t2v_1.3B_fp16.safetensors" not in comfy.models("diffusion_models"):
        raise FactoryError("MODEL_MISSING", "Wan 2.1 is not downloaded (Models tab).")
    W = lambda name: os.path.join(work, name)                 # noqa: E731

    # ---- 1. voices (they set each shot's length)
    job.stage("voice", "Voices")
    cvoices = comfy.voices() if needs_comfy else {}
    char_voice = {c["name"].lower(): c.get("voice", "") for c in sc["characters"]}
    narrator = o.get("voice_override") or sc["narrator"] or o["voice"]
    if narrator not in cvoices and not voice_lookup(narrator, {})[1]:
        narrator = o["voice"]
    if o.get("own_voice") and narrator == "own":
        narrator = o["own_voice"]
    with_voice = [sh for sh in todo if sh["voice"]]
    if with_voice and needs_comfy and not comfy.has_node("UnifiedTTSTextNode"):
        raise FactoryError("NODE_MISSING", "The TTS-Audio-Suite node pack is missing in ComfyUI (needed for voices).")
    for k, sh in enumerate(with_voice):
        who = (char_voice.get(sh["speaker"].lower(), "") or sh["speaker"]) if sh["speaker"] else ""
        if who == "own":
            who = o.get("own_voice", "")
        vname = who if (who in cvoices or voice_lookup(who, {})[1]) else narrator
        vpath, own = voice_lookup(vname, cvoices)
        if not vpath and not own:
            vpath = next(iter(cvoices.values()), comfy.FALLBACK_VOICES["female_01"])
        dest = W(f"{sh['id']}_voice.wav")
        key = _key(sh["voice"], vpath, file_sig(own) if own else "", o["voice_language"], o["voice_emotion"],
                   o["voice_speed"], sh["seed"])
        job.step(k, len(with_voice), f"Voice {k + 1}/{len(with_voice)} (scene {sh['scene']})")
        job.at(sh, voice=vname)
        if not cache.fresh(dest, key):
            log(f"voice {sh['id']}: {vname} '{sh['voice'][:60]}'")
            raw = _gpu(job, comfy.tts, sh["voice"], W(f"{sh['id']}_voice_raw.flac"), vpath, own_voice_path=own,
                       exaggeration=float(o["voice_emotion"]), seed=sh["seed"], language=o["voice_language"])
            media.prepare_voice(raw, dest, float(o["voice_speed"]))
            cache.put(dest, key)

    for sh in shots:
        vf = W(f"{sh['id']}_voice.wav")
        sh["vd"] = media.duration(vf) if sh["voice"] and os.path.exists(vf) else 0.0
        want = sh["seconds"] or (0 if sh["vd"] else min(3.0, cap_s))
        sh["dur"] = round(max(1.5, want, (sh["vd"] + LEAD + TAIL) if sh["vd"] else 0), 2)
    for j, sh in enumerate(shots):
        sh["clip_len"] = round(sh["dur"] + (OVERLAP if crossfade and j < n - 1 else 0), 3)

    # ---- 2. pictures
    job.stage("image", "Pictures")
    need_img = [sh for sh in todo if sh["engine"] in ("still", "ltx_i2v")]
    w, h = q["img"]
    for k, sh in enumerate(need_img):
        prompt = _shot_prompt(sc, sh) + " Highly detailed, sharp focus, vertical composition."
        dest = W(f"{sh['id']}_img.png")
        key = _key(prompt, img_ckpt, sh["seed"], w, h, q["img_steps"])
        job.step(k, len(need_img), f"Picture {k + 1}/{len(need_img)} (scene {sh['scene']})")
        job.at(sh, model=img_ckpt)
        if not cache.fresh(dest, key):
            log(f"image {sh['id']}: {prompt[:120]}")
            _gpu(job, comfy.sdxl_image, prompt, dest, img_ckpt, seed=sh["seed"], width=w, height=h, steps=q["img_steps"])
            cache.put(dest, key)

    # ---- 3. animation (a shot longer than one clip continues from its own last frame)
    job.stage("video", "Animation")
    need_vid = [sh for sh in todo if sh["engine"] in ("ltx_i2v", "ltx_t2v", "wan")]
    vw, vh = q["vid"]
    for k, sh in enumerate(need_vid):
        e = sh["engine"]
        prompt = _shot_prompt(sc, sh, with_motion=True)
        dest = W(f"{sh['id']}_raw.mp4")
        img = W(f"{sh['id']}_img.png")
        gen_len = sh["dur"] + OVERLAP          # same with or without crossfade, so previews reuse it
        segs = 1 if e == "wan" else max(1, min(4, math.ceil(gen_len / (cap_s * 1.3))))
        seg_s = gen_len / segs
        key = _key(e, prompt, vid_ckpt, sh["seed"], vw, vh, q["vid_steps"], min(seg_s, cap_s), segs,
                   file_sig(img) if e == "ltx_i2v" else "")
        job.step(k, len(need_vid), f"Animating {k + 1}/{len(need_vid)} (scene {sh['scene']})")
        job.at(sh, engine=e, model=vid_ckpt, segments=segs)
        if cache.fresh(dest, key):
            continue
        log(f"video {sh['id']} {e} x{segs}: {prompt[:120]}")
        try:
            parts = []
            for g in range(segs):
                out = dest if segs == 1 else W(f"{sh['id']}_seg{g}.mp4")
                if e == "wan":
                    _gpu(job, comfy.wan_video, prompt, out, seconds=seg_s, seed=sh["seed"])
                elif e == "ltx_t2v" and g == 0:
                    _gpu(job, comfy.ltx_video, prompt, out, vid_ckpt, seconds=seg_s, seed=sh["seed"], width=vw,
                         height=vh, steps=q["vid_steps"], cap=cap)
                else:
                    start = img if g == 0 else media.last_frame(parts[-1], W(f"{sh['id']}_seg{g}_start.png"))
                    _gpu(job, comfy.ltx_animate, start, prompt, out, vid_ckpt, seconds=seg_s, seed=sh["seed"] + g,
                         width=vw, height=vh, steps=q["vid_steps"], cap=cap)
                parts.append(out)
            if segs > 1:
                media.join_segments(parts, dest)
            cache.put(dest, key)
        except Exception as ex:
            if e == "ltx_i2v" and os.path.exists(img) and errors.classify(ex) not in ("CANCELLED", "COMFY_DOWN"):
                job.warn(f"Scene {sh['scene']}: animation failed ({str(ex)[:120]}); using a camera move on the picture.",
                         ex)
                sh["engine"] = "still"
            else:
                raise

    # ---- 4. clips at 1080x1920, exact length
    job.stage("clip", "Cutting clips")
    fade = 0.12 if o.get("transition") == "fade" else 0.0
    look = o.get("look", "film")
    for k, sh in enumerate(todo):
        j = shots.index(sh)
        job.at(sh, engine=sh["engine"])
        dest = W(f"{sh['id']}_clip.mp4")
        fi = fade if j > 0 else 0.0
        fo = 0.6 if j == n - 1 else fade
        if sh["engine"] == "card":
            key = _key("card", sh["clip_len"], fi, fo, look)
            if not cache.fresh(dest, key):
                media.card_clip(dest, sh["clip_len"], look, fi, fo)
                cache.put(dest, key)
            continue
        own = sopt(sh["scene"]).get("media", "")
        own_path = os.path.join(pdir, own) if own else ""
        src = {"still": W(f"{sh['id']}_img.png"), "own": own_path}.get(sh["engine"], W(f"{sh['id']}_raw.mp4"))
        if sh["engine"] == "own" and not (own_path and os.path.exists(own_path)):
            raise FactoryError("OWN_MEDIA", f"Scene {sh['scene']}: 'my own clip' is chosen but no file is uploaded for it.")
        kb = media.KB_CYCLE[j % len(media.KB_CYCLE)]
        key = _key(sh["engine"], file_sig(src), sh["clip_len"], sh["motion"], fi, fo, look, kb)
        job.step(k, len(todo), f"Clip {k + 1}/{len(todo)}")
        if not cache.fresh(dest, key):
            if media.is_image(src):
                media.still_to_clip(src, dest, sh["clip_len"], sh["motion"], fi, fo, look, kb)
            else:
                media.fit_clip(src, dest, sh["clip_len"], fi, fo, look=look)
            cache.put(dest, key)

    if only_scene:
        return _preview(slug, sc, shots, todo, work, o, cache, job, q)

    # ---- 5. sound: effect beds + music per chapter
    job.stage("sound", "Sound")
    job.at()
    t = 0.0
    for sh in shots:
        sh["start"] = t
        t += sh["dur"]
    total = round(t, 2)
    sfx_items = _sfx_beds(sc, shots, work, o, cache, job)
    music = _music(sc, shots, total, work, o, cache, job)

    # ---- 6. final video: one encode with crossfades, subtitles and sound
    job.stage("final", "Putting it together")
    job.at()
    for sh in shots:
        if not os.path.exists(W(f"{sh['id']}_clip.mp4")):
            raise FactoryError("SCRIPT", f"Missing clip {sh['id']}: render the whole video, not one scene.")
    cues = []
    font = media.pick_font(o.get("sub_font", ""))
    chapter_starts = []
    for j, sh in enumerate(shots):
        first_of_chapter = sh["chapter"] >= 0 and (j == 0 or shots[j - 1]["chapter"] != sh["chapter"])
        if first_of_chapter and j > 0:
            chapter_starts.append(j)
        if sh["engine"] == "card":
            cues.append({"kind": "card", "start": sh["start"] + 0.15, "end": sh["start"] + sh["dur"] - 0.1,
                         "text": sh["text"]})
            continue
        text = sh["text"] or (sc["hook"] if j == 0 else "")
        if text and o.get("onscreen_text", True):
            cues.append({"kind": "big", "start": sh["start"] + (0 if j == 0 else 0.1),
                         "end": sh["start"] + sh["dur"] - 0.05, "text": text})
        elif first_of_chapter and o.get("chapter_titles", True) and sc["chapters"][sh["chapter"]]["title"]:
            cues.append({"kind": "big", "start": sh["start"] + 0.3, "end": sh["start"] + min(sh["dur"], 2.8),
                         "text": sc["chapters"][sh["chapter"]]["title"]})
        if sh["vd"] and o.get("subtitles", True):
            cues += media.caption_cues(sh["voice"], sh["start"] + LEAD, sh["vd"])
    ass = media.write_ass(W("subs.ass"), cues, font, media.pick_serif()) if cues else None
    job.step(1, 4, "Mixing sound")
    voices = [(W(f"{sh['id']}_voice.wav"), sh["start"] + LEAD) for sh in shots if sh["vd"]]
    audio = media.mix(total, voices, sfx_items, music, W("audio.wav"), float(o["music_volume"]))
    job.step(2, 4, f"Encoding {total / 60:.1f} min of video")
    final = media.assemble([W(f"{sh['id']}_clip.mp4") for sh in shots], [sh["dur"] for sh in shots],
                           os.path.join(pdir, "final.mp4"), work, audio, ass,
                           transition="crossfade" if crossfade else "cut", overlap=OVERLAP,
                           chapter_starts=chapter_starts, crf=q["crf"],
                           on_step=lambda a, b: job.step(2, 4, f"Crossfading part {a}/{b}"))
    pic = next((sh for sh in shots if sh["engine"] != "card"), shots[0])
    media.cover(W(f"{pic['id']}_clip.mp4"), min(pic["dur"] * 0.6, 2.0), sc["hook"] or sc["title"],
                os.path.join(pdir, "cover.jpg"), work, font)
    caption = (sc["caption"] + "\n\n" + " ".join(sc["hashtags"])).strip()
    with open(os.path.join(pdir, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(caption + "\n")
    # TikTok hangs on a re-used file name: every export gets a fresh one
    stamp = datetime.datetime.now()
    base = projects.export_name(meta.get("output_name", ""), slug, stamp)
    shutil.copyfile(final, os.path.join(config.EXPORTS_DIR, base + ".mp4"))
    shutil.copyfile(os.path.join(pdir, "cover.jpg"), os.path.join(config.EXPORTS_DIR, base + ".jpg"))
    with open(os.path.join(config.EXPORTS_DIR, base + ".txt"), "w", encoding="utf-8") as f:
        f.write(caption + "\n")
    meta = projects.load_meta(slug)
    meta["rendered"] = stamp.isoformat(timespec="seconds")
    meta["duration"] = round(media.duration(final), 1)
    meta["exports"] = (meta.get("exports") or []) + [base + ".mp4"]
    meta["last_quality"] = qname
    meta["shots"] = n
    projects.save_meta(slug, meta)
    return {"final": f"projects/{slug}/final.mp4", "export": f"exports/{base}.mp4", "seconds": meta["duration"],
            "shots": n}


def _shot_prompt(sc, sh, with_motion=False):
    p = scriptfmt.visual_prompt(sc, sh, with_motion)
    return f"{sh['frame']}. {p}" if sh.get("frame") else p


def _preview(slug, sc, shots, todo, work, o, cache, job, q):
    """One scene with its own sound, for the storyboard."""
    i = todo[0]["scene"]
    t = 0.0
    for sh in todo:
        sh["start"] = t
        t += sh["dur"]
    voices = [(os.path.join(work, f"{sh['id']}_voice.wav"), sh["start"] + LEAD) for sh in todo if sh["vd"]]
    sfx = _sfx_beds(sc, todo, work, o, cache, job, prefix="prev_")
    audio = media.mix(t, voices, sfx, None, os.path.join(work, f"s{i:02d}_preview.wav"))
    prev = os.path.join(work, f"s{i:02d}_preview.mp4")
    media.assemble([os.path.join(work, f"{sh['id']}_clip.mp4") for sh in todo], [sh["dur"] for sh in todo],
                   prev, work, audio, None, transition="cut", crf=q["crf"])
    return {"preview": f"projects/{slug}/work/s{i:02d}_preview.mp4", "shots": len(todo)}


def _sfx_beds(sc, shots, work, o, cache, job, prefix=""):
    """Sound effects as continuous beds: the same sound over neighbouring shots plays on instead of
    restarting at every cut. AI effects (Stable Audio) stay per scene."""
    if not o.get("sfx", True):
        return []
    vol = float(o.get("sfx_volume", 0.35))
    items = []
    ai = o.get("sfx_engine") == "ai" and "stable-audio-open-1.0.safetensors" in comfy.models("checkpoints")
    files = {}
    for sh in shots:
        if not sh["sound"]:
            files[sh["id"]] = []
            continue
        if ai:
            dest = os.path.join(work, f"s{sh['scene']:02d}_sfx_ai.flac")
            key = _key(sh["sound"])
            if not cache.fresh(dest, key):
                try:
                    comfy.stable_audio_sfx(sh["sound"], dest, seconds=10.0)
                    cache.put(dest, key)
                except Exception as e:
                    job.warn(f"Scene {sh['scene']}: AI sound effect failed ({str(e)[:100]}); using built-in sounds.", e)
                    ai = False
            if ai:
                files[sh["id"]] = [dest]
                continue
        files[sh["id"]] = media.find_sfx(sh["sound"])
    for j, sh in enumerate(shots):
        for f in files[sh["id"]]:
            if j > 0 and f in files[shots[j - 1]["id"]]:
                continue                       # the bed that started earlier plays on
            end = j
            while end + 1 < len(shots) and f in files[shots[end + 1]["id"]]:
                end += 1
            length = sum(x["dur"] for x in shots[j:end + 1])
            dest = os.path.join(work, f"{prefix}bed_{sh['id']}_{os.path.splitext(os.path.basename(f))[0]}.wav")
            key = _key(file_sig(f), length, vol, len(files[sh["id"]]))
            if not cache.fresh(dest, key):
                media.sfx_track(f, dest, length, vol / max(1, len(files[sh["id"]])) * 1.3)
                cache.put(dest, key)
            items.append((dest, sh["start"]))
    if sc.get("ambience") and shots:
        # the script's overall soundscape (AMBIENCE / AUDIO line) runs under the whole video, softer
        total = sum(sh["dur"] for sh in shots)
        for f in media.find_sfx(sc["ambience"]):
            if any(f in files[sh["id"]] for sh in shots):
                continue                       # the scenes already use this sound
            dest = os.path.join(work, f"{prefix}amb_{os.path.splitext(os.path.basename(f))[0]}.wav")
            key = _key(file_sig(f), total, vol)
            if not cache.fresh(dest, key):
                media.sfx_track(f, dest, total, vol * 0.6)
                cache.put(dest, key)
            items.append((dest, shots[0]["start"]))
    return items


def _sections(sc, shots, total):
    """Music sections: chapters, or ~75 s blocks for long videos without chapters."""
    if sc["chapters"]:
        secs, cur = [], None
        for sh in shots:
            if cur is None or sh["chapter"] != cur["chapter"]:
                cur = {"chapter": sh["chapter"], "dur": 0.0}
                secs.append(cur)
            cur["dur"] += sh["dur"]
        return secs
    if total <= 150:
        return [{"chapter": -1, "dur": total}]
    secs, cur = [], {"chapter": -1, "dur": 0.0}
    for sh in shots:
        if cur["dur"] >= 75:
            secs.append(cur)
            cur = {"chapter": -1, "dur": 0.0}
        cur["dur"] += sh["dur"]
    secs.append(cur)
    return secs


def _music(sc, shots, total, work, o, cache, job, xf=3.0):
    mode = o.get("music", "builtin")
    if mode == "none":
        return None
    dest = os.path.join(work, "music.wav")
    secs = _sections(sc, shots, total)
    files = media.music_files()
    ace = first_model("checkpoints", "", lambda f: "ace_step" in f) if mode == "ace" else ""
    if mode == "ace" and not ace:
        job.warn("ACE-Step is not downloaded; using the built-in music.")
    if mode == "file" and not files:
        job.warn("The music folder is empty; using the built-in music.")
    key = _key(mode, o.get("music_file"), [(s["chapter"], round(s["dur"], 2)) for s in secs], sc["music"],
               [c["music"] for c in sc["chapters"]], files if mode == "file" else "", ace)
    if cache.fresh(dest, key):
        return dest
    parts = []
    start = random.randrange(len(files)) if files else 0
    for k, sec in enumerate(secs):
        length = sec["dur"] + (xf if k < len(secs) - 1 else 0) + 0.5
        mood_text = (sc["chapters"][sec["chapter"]]["music"] if sec["chapter"] >= 0 else "") or sc["music"]
        part = os.path.join(work, f"music_part{k:02d}.wav")
        if mode == "file" and files:
            name = o.get("music_file") if o.get("music_file") in files else files[(start + k) % len(files)]
            media.file_music(name, length, part)
        elif mode == "ace" and ace:
            try:
                raw = comfy.ace_music((mood_text or "cinematic ambient, soft strings") + ", instrumental",
                                      os.path.join(work, f"music_raw{k:02d}.flac"), ace, seconds=min(240, length))
                media.fit_music(raw, length, part)
            except Exception as e:
                job.warn(f"AI music failed ({str(e)[:120]}); using the built-in music for this part.", e)
                media.builtin_music(media.mood_of(mood_text + " " + sc["style"]), length, part)
        else:
            shift = [0, -2, 3, -4, 2, 5, -1][k % 7] if len(secs) > 1 else 0
            media.builtin_music(media.mood_of(mood_text + " " + sc["style"]), length, part, shift)
        parts.append(part)
    media.join_music(parts, dest, total, xf)
    cache.put(dest, key)
    return dest


# ------------------------------------------------------------------ CLI
def make(file, engine="", quality="", voice="", music="", name="", output=""):
    """Text file -> new project -> finished video. Returns the render result or None."""
    with open(file, encoding="utf-8") as f:
        text = f.read()
    sc = scriptfmt.parse(text)
    if not sc["scenes"]:
        sys.exit("No scenes found in the file. See templates/video-template.txt for the format.")
    for w in scriptfmt.check(sc)["warnings"]:
        print("note:", w)
    slug = projects.create(text, name or "", output or "")
    meta = projects.load_meta(slug)
    meta["options"] = {k: v for k, v in (("engine", engine), ("quality", quality),
                                         ("voice_override", voice), ("music", music)) if v}
    projects.save_meta(slug, meta)
    print(f"project: projects/{slug}  (progress: projects/{slug}/render.log)")
    out = render(slug)
    if out:
        print(f"\nVIDEO:   {os.path.join(config.ROOT, out['final'])}\nEXPORT:  {os.path.join(config.ROOT, out['export'])}"
              f"\nCAPTION: {projects.path(slug, 'caption.txt')}")
    return out


def main():
    ap = argparse.ArgumentParser(description="Render a video project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render projects/<slug>")
    r.add_argument("slug")
    r.add_argument("--scene", type=int, default=0, help="only this scene (preview)")
    m = sub.add_parser("make", help="script file -> new project -> video")
    m.add_argument("file")
    for opt in ("engine", "quality", "voice", "music", "name", "output"):
        m.add_argument("--" + opt, default="")
    a = ap.parse_args()
    if a.cmd == "render":
        render(a.slug, a.scene or None)
    else:
        make(a.file, a.engine, a.quality, a.voice, a.music, a.name, a.output)


if __name__ == "__main__":
    main()

"""Local LLM via Ollama (stdlib only): write a story from an idea, convert a pasted script,
improve a script. Output is the factory's script dict (see scriptfmt.py).

Small local models write better in two passes: first the story beats as plain text (streamed,
so the GUI shows it being written), then a JSON storyboard forced by a JSON schema.
"""
import json
import re
import urllib.request

import scriptfmt

URL = "http://127.0.0.1:11434"


def configure(url):
    global URL
    URL = (url or URL).rstrip("/")


def _post(path, data, timeout=30):
    r = urllib.request.Request(URL + path, data=json.dumps(data).encode(),
                               headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=timeout)


def alive():
    try:
        urllib.request.urlopen(URL + "/api/version", timeout=3).read()
        return True
    except Exception:
        return False


def installed():
    """[{name, size_gb, family, params}] of local models (embedding models left out)."""
    try:
        with urllib.request.urlopen(URL + "/api/tags", timeout=5) as r:
            ms = json.loads(r.read()).get("models", [])
    except Exception:
        return []
    out = []
    for m in ms:
        d = m.get("details") or {}
        fam = (d.get("family") or "").lower()
        if "bert" in fam or "embed" in m["name"]:
            continue
        out.append({"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1), "family": fam,
                    "params": d.get("parameter_size", ""), "quant": d.get("quantization_level", "")})
    return out


def unload_all():
    """Free the GPU for ComfyUI: unload every model Ollama holds."""
    try:
        with urllib.request.urlopen(URL + "/api/ps", timeout=5) as r:
            for m in json.loads(r.read()).get("models", []):
                _post("/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30).read()
    except Exception:
        pass


def capabilities(model):
    try:
        with _post("/api/show", {"model": model}, timeout=10) as r:
            return json.loads(r.read()).get("capabilities", [])
    except Exception:
        return []


def pick_model(preferred, vram_gb):
    """User's choice if installed, else the biggest installed model that fits the GPU."""
    ms = installed()
    names = [m["name"] for m in ms]
    if preferred and preferred in names:
        return preferred
    ok = [m for m in ms if m["size_gb"] <= max(1.5, vram_gb - 1.0) and "r1" not in m["name"]]
    ok = ok or sorted(ms, key=lambda m: m["size_gb"])[:1]
    return max(ok, key=lambda m: m["size_gb"])["name"] if ok else ""


def pull(name, on_progress=None, should_stop=None):
    """ollama pull with progress (done_bytes, total_bytes, status)."""
    with _post("/api/pull", {"model": name, "stream": True}, timeout=60) as r:
        for line in r:
            if should_stop and should_stop():
                raise RuntimeError("cancelled")
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            if on_progress:
                on_progress(ev.get("completed", 0), ev.get("total", 0), ev.get("status", ""))
    return True


def delete(name):
    r = urllib.request.Request(URL + "/api/delete", data=json.dumps({"model": name}).encode(), method="DELETE",
                               headers={"Content-Type": "application/json"})
    urllib.request.urlopen(r, timeout=30).read()


def chat(model, system, user, schema=None, on_text=None, should_stop=None, ctx=8192, temperature=0.8, timeout=900):
    """Streamed chat. Returns the full text. keep_alive 0 hands the GPU back afterwards."""
    body = {"model": model, "stream": True, "keep_alive": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"num_ctx": int(ctx), "temperature": temperature}}
    if schema:
        body["format"] = schema
    if "thinking" in capabilities(model):
        body["think"] = False
    text = ""
    with _post("/api/chat", body, timeout=timeout) as r:
        for line in r:
            if should_stop and should_stop():
                raise RuntimeError("cancelled")
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            text += (ev.get("message") or {}).get("content", "")
            if on_text:
                on_text(text)
            if ev.get("done"):
                break
    return text


# ------------------------------------------------------------------ storytelling prompts
SCENE_ITEM = {"type": "object", "properties": {
    "seconds": {"type": "number"}, "visual": {"type": "string"}, "motion": {"type": "string"},
    "speaker": {"type": "string"}, "line": {"type": "string"}, "text": {"type": "string"}, "sound": {"type": "string"}},
    "required": ["seconds", "visual", "motion", "speaker", "line", "text", "sound"]}


def _voice_field(voices):
    vs = list(voices) + ["own"] if voices else None
    return {"type": "string", "enum": vs} if vs else {"type": "string"}


def outline_schema(voices, n=None):
    vf = _voice_field(voices)
    count = {"minItems": n, "maxItems": n} if n else {}
    return {"type": "object", "properties": {
        "title": {"type": "string"}, "logline": {"type": "string"}, "style": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "look": {"type": "string"}, "voice": vf}, "required": ["name", "look", "voice"]}},
        "narrator": vf, "music": {"type": "string"}, "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}}, "hook": {"type": "string"},
        "chapters": {"type": "array", **count, "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "summary": {"type": "string"}, "music": {"type": "string"}},
            "required": ["title", "summary", "music"]}}},
        "required": ["title", "logline", "style", "characters", "narrator", "music", "caption", "hashtags", "hook",
                     "chapters"]}


CHAPTER_SCHEMA = {"type": "object", "properties": {"scenes": {"type": "array", "items": SCENE_ITEM}},
                  "required": ["scenes"]}


def schema(voices):
    vs = list(voices) + ["own"] if voices else None
    voice_field = {"type": "string", "enum": vs} if vs else {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "style": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "look": {"type": "string"}, "voice": voice_field},
                "required": ["name", "look", "voice"]}},
            "narrator": voice_field,
            "music": {"type": "string"},
            "caption": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "hook": {"type": "string"},
            "scenes": {"type": "array", "items": {"type": "object", "properties": {
                "seconds": {"type": "number"}, "visual": {"type": "string"}, "motion": {"type": "string"},
                "speaker": {"type": "string"}, "line": {"type": "string"}, "text": {"type": "string"},
                "sound": {"type": "string"}},
                "required": ["seconds", "visual", "motion", "speaker", "line", "text", "sound"]}},
        },
        "required": ["title", "style", "characters", "narrator", "music", "caption", "hashtags", "hook", "scenes"],
    }


STORY_SYSTEM = """You are a top short-form video storyteller who writes viral TikTok micro-stories.
You know what keeps people watching:
- The first 2 seconds hook: a shocking image, a mystery, or a question the viewer needs answered.
- ONE clear story: one main character, one problem, rising tension, and a twist or payoff at the end
  that makes people rewatch or comment. Show, don't explain.
- Short spoken lines (max 12 words each), simple words, strong verbs, present tense when possible.
- Every scene is one strong picture that a camera could film in 3-5 seconds.
Write in plain text, no markdown."""

BEATS_USER = """Write the story for a {seconds}-second vertical video in {n} scenes.
Idea: {idea}
Genre / tone: {genre}
{narration}
{extra}
Answer in this shape (plain text):
TITLE: ...
LOGLINE: one sentence
CHARACTERS: name = fixed look (age, face, hair, clothes), one per line (or "none")
SCENES:
1. picture: ... | spoken: ... | on-screen: ... | sound: ...
2. ...
TWIST: what the last scene reveals"""

BOARD_SYSTEM = """You turn a short story into a storyboard for an AI video factory.
The pictures are made by an image model (SDXL) and animated by a video model (LTX), so:
- "visual": a self-contained image prompt in English, 20-45 words: subject + action + place +
  lighting + shot type (wide shot, close up, low angle ...). Repeat the place in every scene.
  Use the character's NAME in the visual whenever they appear (their look is added automatically).
  NEVER ask for written words, signs, screens, letters, logos or UI. No split screens.
- "motion": one camera move + what moves in the shot, e.g. "slow push in, hair blowing in the wind".
- "line": the WORDS spoken in this scene (max 12 words), or "" for silence. Never a voice name.
  "speaker" is the character's name if a character says the line, or "" for the narrator.
- "text": big on-screen words (max 6) ONLY in scene 1 (the hook) and at most one big reveal;
  every other scene has "". Never stage directions.
- "sound": 1-3 sound effects (wind, rain, waves, thunder, footsteps, heartbeat, clock, whoosh,
  impact, riser, static, fire, rumble ...), or "".
- "seconds": 2.5 to 6; a voice line needs about 1 second per 2.6 words plus 0.5.
- "style": ONE visual style for all shots (medium, lighting, colours, lens, era).
- "music": mood + instruments + tempo for the background music.
- "caption": the TikTok description, 1-2 sentences, ends with a question. "hashtags": 5-8 tags.
- "hook": the scene-1 on-screen words (max 6).
- "narrator" and each character's "voice" are voice names from this list: {voices}."""

BOARD_USER = """Make the storyboard JSON for this story ({n} scenes, about {seconds} seconds):

{story}"""

CONVERT_USER = """Turn this material into the storyboard JSON. Keep the author's story, scene order and
spoken lines (shorten only lines over 15 words by splitting them across scenes). Fill every missing
field yourself (visual prompts, motion, sound, caption, hashtags, style, music).

MATERIAL:
{text}"""

IMPROVE_USER = """Improve this storyboard and return the full improved storyboard JSON.
What to improve: {instruction}
Keep what already works. Same language.

CURRENT SCRIPT:
{text}"""

NARRATION = {
    "narrator": "Tell it with a narrator voice.",
    "dialogue": "Tell it through the characters' own spoken lines (no narrator).",
    "mixed": "Mix narrator lines with a few short character lines.",
    "none": "No spoken words at all: tell it with pictures, on-screen text and sound.",
}


def _scenes_for(seconds):
    return max(3, min(12, round(float(seconds) / 4.5)))


def _board(model, story, seconds, voices, on_text, should_stop, ctx):
    n = _scenes_for(seconds)
    raw = chat(model, BOARD_SYSTEM.format(voices=", ".join(voices) or "female_01"),
               BOARD_USER.format(n=n, seconds=seconds, story=story), schema=schema(voices),
               on_text=on_text, should_stop=should_stop, ctx=ctx, temperature=0.6)
    return _to_script(raw)


def _to_script(raw):
    try:
        js = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("The model did not return a storyboard. Try again or pick another LLM.")
        js = json.loads(raw[start:end + 1])
    sc = scriptfmt.from_json(js)
    _tidy(sc)
    if not sc["scenes"]:
        raise RuntimeError("The model returned no scenes. Try again or pick a bigger LLM.")
    return sc


def short_hook(text, words=7):
    """On-screen hook: first clause, at most `words` words."""
    t = re.split(r"(?<=[.!?…])\s", (text or "").strip())[0]
    w = t.split()
    return " ".join(w[:words]).rstrip(",;:") + ("..." if len(w) > words else "")


def _tidy(sc):
    """Small models over-use on-screen text and repeat hashtags in the caption."""
    texts = [i for i, s in enumerate(sc["scenes"]) if s["text"]]
    keep = {0, texts[-1]} if len(texts) > 2 else set(texts)
    for i, s in enumerate(sc["scenes"]):
        if i not in keep:
            s["text"] = ""
        if s["voice"] and re.fullmatch(r"[\w\-./]+", s["voice"]) and "_" in s["voice"]:
            s["voice"] = ""        # a voice NAME landed in the spoken line
    sc["hook"] = short_hook(sc["hook"])
    if sc["scenes"] and sc["hook"] and not sc["scenes"][0]["text"]:
        sc["scenes"][0]["text"] = sc["hook"]
    if sc["hashtags"]:
        sc["caption"] = re.sub(r"\s*#[\w]+", "", sc["caption"]).strip()


OUTLINE_SYSTEM = """You plan long story videos (2-15 minutes) told in many short scenes, for TikTok and
YouTube. You know what keeps people watching for minutes:
- A hook in the first seconds, then a promise of what is coming.
- Chapters of about one minute; each has its own small climax and ends on a question or reveal
  that makes the viewer stay for the next one.
- One main character the viewer cares about, a clear goal, rising stakes, and a strong ending.
Answer as JSON."""

OUTLINE_USER = """Plan a {minutes}-minute vertical story video in exactly {chapters} chapters.
Idea: {idea}
Genre / tone: {genre}
{narration}
{extra}
Give EXACTLY {chapters} chapters, in story order from the opening to the ending. For each chapter: a short
title (2-5 words, no "Chapter" word), a 2-3 sentence summary of what NEW happens in it, and the music mood.
"hook" = max 6 words shown on screen at the very start.
"style" is ONE visual style for all shots. Characters get a fixed look (age, face, hair, clothes).
Voices you may use: {voices}."""

CHAPTER_USER = """STORY BIBLE
Title: {title}
Logline: {logline}
Style: {style}
Characters: {characters}
{narration}

ALL CHAPTERS
{outline}

{previous}
Never repeat an event or a sentence from the story so far: this chapter moves the story FORWARD.
Now write CHAPTER {k} of {chapters}: "{ctitle}". What happens: {summary}
Write exactly {n} scenes (about {secs} seconds in total). Continue the story and narration seamlessly
from where the previous chapter ended; do not repeat it, do not end the whole story before the last chapter.
{last}"""


def _scene_line(s):
    return (s.get("speaker") + ": " if s.get("speaker") else "") + (s.get("voice") or "")


def write_long(model, idea, seconds, genre="", narration="narrator", extra="", voices=(),
               on_text=None, on_stage=None, should_stop=None, ctx=8192):
    """2-15 minute videos: an outline with chapters, then one storyboard call per chapter that gets
    the story bible, the outline and the end of the previous chapter (small models lose the
    thread if asked for 150 scenes at once)."""
    chapters = max(2, min(15, round(seconds / 60)))
    per_secs = seconds / chapters
    per_n = max(5, round(per_secs / 4.8))
    vs = ", ".join(voices) or "female_01"
    if on_stage:
        on_stage(f"Planning {chapters} chapters")
    ol, chs = {}, []
    for attempt in range(2):          # small models sometimes give fewer chapters: ask once more
        raw = chat(model, OUTLINE_SYSTEM, OUTLINE_USER.format(
            minutes=round(seconds / 60, 1), chapters=chapters, idea=idea or "surprise me with a gripping original idea",
            genre=genre or "any, whatever fits the idea best", narration=NARRATION.get(narration, ""),
            extra=f"Extra wishes: {extra}" if extra else "", voices=vs),
            schema=outline_schema(voices, chapters), on_text=on_text, should_stop=should_stop, ctx=ctx,
            temperature=0.8)
        try:
            got = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        except ValueError:
            continue
        got_chs = [c for c in got.get("chapters") or [] if isinstance(c, dict) and c.get("summary")]
        if len(got_chs) > len(chs):
            ol, chs = got, got_chs[:chapters]
        if len(chs) >= chapters:
            break
    if not chs:
        raise RuntimeError("The model did not return an outline. Try again or pick another LLM.")
    for c in chs:
        c["title"] = re.sub(r"^\s*(chapter|part|act)\s*\w*\s*[:.\-–]\s*", "", str(c.get("title", "")), flags=re.I).strip()
    per_secs = seconds / len(chs)
    per_n = max(5, round(per_secs / 4.8))
    outline_txt = "\n".join(f"{i + 1}. {c.get('title', '')}: {c.get('summary', '')}" for i, c in enumerate(chs))
    chars = "; ".join(f"{c.get('name')} = {c.get('look')}" for c in ol.get("characters") or [] if isinstance(c, dict)) or "none"
    head = scriptfmt.from_json({k: ol.get(k) for k in ("title", "style", "characters", "narrator", "music",
                                                        "caption", "hashtags", "hook")})
    head["chapters"] = [{"title": c.get("title", ""), "music": c.get("music", "")} for c in chs]
    scenes = []
    system = BOARD_SYSTEM.format(voices=vs)
    for k, c in enumerate(chs):
        if should_stop and should_stop():
            raise RuntimeError("cancelled")
        if on_stage:
            on_stage(f"Writing chapter {k + 1}/{len(chs)}: {c.get('title', '')}")
        prev = [s for s in scenes if s["chapter"] == k - 1][-3:]
        so_far = " ".join(_scene_line(s) for s in scenes if s["voice"])
        so_far = so_far[-2500:]
        previous = (f"THE STORY SO FAR (spoken): ...{so_far}\n\nTHE PREVIOUS CHAPTER ENDED WITH:\n" + "\n".join(
            f"- picture: {s['visual'][:140]} | said: {_scene_line(s)}" for s in prev)) if prev else \
            "This is the opening chapter: scene 1 is the hook."
        last = "This is the LAST chapter: give the story its twist / payoff and a satisfying ending." \
            if k == len(chs) - 1 else "End this chapter on a small cliffhanger or reveal."
        raw = chat(model, system, CHAPTER_USER.format(
            title=head["title"], logline=ol.get("logline", ""), style=head["style"], characters=chars,
            narration=NARRATION.get(narration, ""), outline=outline_txt, previous=previous, k=k + 1,
            chapters=len(chs), ctitle=c.get("title", ""), summary=c.get("summary", ""), n=per_n,
            secs=round(per_secs), last=last),
            schema=CHAPTER_SCHEMA, on_text=on_text, should_stop=should_stop, ctx=ctx, temperature=0.75)
        try:
            part = scriptfmt.from_json({"characters": ol.get("characters") or [],
                                        "scenes": json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("scenes", [])})
        except ValueError:
            raise RuntimeError(f"Chapter {k + 1} came back broken. Try again or pick another LLM.") from None
        for s in part["scenes"]:
            s["chapter"] = k
            if narration == "none":
                s["voice"], s["speaker"] = "", ""
            if s["voice"] and re.fullmatch(r"[\w\-./]+", s["voice"]) and "_" in s["voice"]:
                s["voice"] = ""
        # on-screen text: the hook in scene 1, at most one reveal per chapter
        texts = [s for s in part["scenes"] if s["text"]]
        for s in texts[:-1]:
            s["text"] = ""
        scenes += part["scenes"]
    head["scenes"] = scenes
    head["hook"] = short_hook(head["hook"] or head["title"])
    if scenes and head["hook"]:
        scenes[0]["text"] = head["hook"]
    if head["hashtags"]:
        head["caption"] = re.sub(r"\s*#[\w]+", "", head["caption"]).strip()
    return scriptfmt.normalize(head), outline_txt


def write_story(model, idea, seconds=30, genre="", narration="narrator", extra="", voices=(),
                on_text=None, on_stage=None, should_stop=None, ctx=8192):
    if float(seconds) > 120:
        return write_long(model, idea, int(seconds), genre, narration, extra, voices, on_text, on_stage,
                          should_stop, ctx)
    n = _scenes_for(seconds)
    if on_stage:
        on_stage("Writing the story")
    story = chat(model, STORY_SYSTEM, BEATS_USER.format(
        seconds=seconds, n=n, idea=idea or "surprise me with a gripping original idea",
        genre=genre or "any, whatever fits the idea best", narration=NARRATION.get(narration, ""),
        extra=f"Extra wishes: {extra}" if extra else ""),
        on_text=on_text, should_stop=should_stop, ctx=ctx, temperature=0.9)
    if on_stage:
        on_stage("Turning the story into scenes")
    sc = _board(model, story, seconds, voices, on_text, should_stop, ctx)
    if narration == "none":
        for s in sc["scenes"]:
            s["voice"], s["speaker"] = "", ""
    return sc, story


CONVERT_PART_USER = """STORY BIBLE (keep it): title "{title}", style "{style}", characters: {characters}.
STORY SO FAR (spoken): ...{so_far}

Turn the NEXT PART of the material into storyboard scenes (part {k} of {parts}). Keep the author's
words and order; one scene per 1-2 sentences of narration; write a picture (visual) for every scene.

NEXT PART:
{text}"""


def _chunks(text, words=450):
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        for sent in (re.split(r"(?<=[.!?…])\s+", p) if len(p.split()) > words else [p]):
            if cur and len((cur + " " + sent).split()) > words:
                out.append(cur)
                cur = sent
            else:
                cur = (cur + ("\n\n" if sent is p else " ") + sent).strip()
    if cur:
        out.append(cur)
    return out


def convert(model, text, voices=(), on_text=None, should_stop=None, ctx=8192, on_stage=None):
    """Any material -> storyboard. Long material is converted in parts (each part becomes a chapter),
    so it never overflows the model's context."""
    if len(text.split()) > 700:
        return _convert_long(model, text, voices, on_text, should_stop, ctx, on_stage)
    raw = chat(model, BOARD_SYSTEM.format(voices=", ".join(voices) or "female_01"),
               CONVERT_USER.format(text=text[:12000]), schema=schema(voices),
               on_text=on_text, should_stop=should_stop, ctx=ctx, temperature=0.5)
    return _to_script(raw)


def _improve_long(model, sc, instruction, voices, on_text, should_stop, ctx, on_stage, block=12):
    """Long scripts: improve block by block (the header and chapters stay)."""
    vs = ", ".join(voices) or "female_01"
    chars = "; ".join(f"{c['name']} = {c['look']}" for c in sc["characters"]) or "none"
    blocks = [sc["scenes"][i:i + block] for i in range(0, len(sc["scenes"]), block)]
    out = []
    for k, bl in enumerate(blocks, 1):
        if should_stop and should_stop():
            raise RuntimeError("cancelled")
        if on_stage:
            on_stage(f"Improving block {k}/{len(blocks)}")
        mini = scriptfmt.empty()
        mini["scenes"] = [dict(x, chapter=-1) for x in bl]
        raw = chat(model, BOARD_SYSTEM.format(voices=vs), IMPROVE_PART_USER.format(
            title=sc["title"], style=sc["style"], characters=chars, k=k, parts=len(blocks), instruction=instruction,
            text=scriptfmt.format_script(mini).split("\n", 1)[1]), schema=CHAPTER_SCHEMA, on_text=on_text,
            should_stop=should_stop, ctx=ctx, temperature=0.6)
        try:
            got = scriptfmt.from_json({"characters": sc["characters"],
                                       "scenes": json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("scenes", [])})["scenes"]
        except ValueError:
            got = []
        if len(got) != len(bl):
            got = bl                              # keep the block as it was rather than lose scenes
        for old, new in zip(bl, got):
            new["chapter"] = old["chapter"]
            if not old["text"]:
                new["text"] = ""
        out += got
    sc["scenes"] = out
    return scriptfmt.normalize(sc)


def _convert_long(model, text, voices, on_text, should_stop, ctx, on_stage):
    parts = _chunks(text)
    vs = ", ".join(voices) or "female_01"
    if on_stage:
        on_stage(f"Converting part 1/{len(parts)}")
    head = convert(model, parts[0], voices, on_text, should_stop, ctx)
    head["chapters"] = [{"title": "", "music": ""}]
    for s in head["scenes"]:
        s["chapter"] = 0
    chars = "; ".join(f"{c['name']} = {c['look']}" for c in head["characters"]) or "none"
    for k, part in enumerate(parts[1:], 2):
        if should_stop and should_stop():
            raise RuntimeError("cancelled")
        if on_stage:
            on_stage(f"Converting part {k}/{len(parts)}")
        so_far = " ".join(_scene_line(s) for s in head["scenes"] if s["voice"])[-1500:]
        raw = chat(model, BOARD_SYSTEM.format(voices=vs), CONVERT_PART_USER.format(
            title=head["title"], style=head["style"], characters=chars, so_far=so_far, k=k, parts=len(parts),
            text=part), schema=CHAPTER_SCHEMA, on_text=on_text, should_stop=should_stop, ctx=ctx, temperature=0.5)
        try:
            got = scriptfmt.from_json({"characters": head["characters"],
                                       "scenes": json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("scenes", [])})
        except ValueError:
            raise RuntimeError(f"Part {k} came back broken. Try again or pick another LLM.") from None
        head["chapters"].append({"title": "", "music": ""})
        for s in got["scenes"]:
            s["chapter"] = len(head["chapters"]) - 1
            if k > 1:
                s["text"] = ""
        head["scenes"] += got["scenes"]
    return scriptfmt.normalize(head)


IMPROVE_PART_USER = """STORY BIBLE (keep it): title "{title}", style "{style}", characters: {characters}.
This is block {k} of {parts} of a long video. Improve ONLY these scenes and return them as scenes JSON,
same number of scenes, same order, same story. What to improve: {instruction}

SCENES:
{text}"""


def improve(model, text, instruction="", voices=(), on_text=None, should_stop=None, ctx=8192, on_stage=None):
    instruction = instruction or ("a stronger hook in scene 1, tighter and punchier spoken lines, more vivid and "
                                  "specific visuals, a more surprising ending, a better caption")
    cur = scriptfmt.parse(text)
    if len(cur["scenes"]) > 24:
        return _improve_long(model, cur, instruction, voices, on_text, should_stop, ctx, on_stage)
    raw = chat(model, BOARD_SYSTEM.format(voices=", ".join(voices) or "female_01"),
               IMPROVE_USER.format(instruction=instruction, text=text[:12000]), schema=schema(voices),
               on_text=on_text, should_stop=should_stop, ctx=ctx, temperature=0.7)
    return _to_script(raw)

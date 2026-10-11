"""The factory's script format: parse (tolerant), format (canonical), check, and the prompt
template you paste into ChatGPT / DeepSeek.

Canonical text:

    TITLE: The Last Lighthouse
    STYLE: cinematic, moody, 35mm film, teal and orange light
    CHARACTER: Mara = 70-year-old lighthouse keeper, grey braid, yellow raincoat | voice: female_02
    NARRATOR: female_01
    MUSIC: slow dark ambient, building tension
    CAPTION: She kept the light on for 40 years. Why?
    HASHTAGS: #story #shortfilm #mystery
    HOOK: Nobody believed her.

    SCENE 1 | 3s
    VISUAL: a lighthouse on a black cliff in a storm, waves exploding below
    MOTION: slow push in
    VOICE: For forty years, Mara kept the light on.
    TEXT: Nobody believed her.
    SOUND: wind, waves

A VOICE line may start with a character name ("Mara: Get inside!") to use that character's
voice. JSON with the same keys is accepted too.
"""
import json
import re

HEADER_KEYS = {
    "title": "title", "name": "title",
    "style": "style", "visual style": "style", "look": "style", "art style": "style",
    "character": "characters", "characters": "characters", "cast": "characters",
    "narrator": "narrator", "voice": "narrator", "narrator voice": "narrator",
    "music": "music", "soundtrack": "music", "score": "music",
    "caption": "caption", "description": "caption", "tiktok caption": "caption",
    "hashtags": "hashtags", "tags": "hashtags",
    "hook": "hook",
    "ambience": "ambience", "atmosphere": "ambience", "ambient sound": "ambience", "background sound": "ambience",
}
SCENE_KEYS = {
    "visual": "visual", "visuals": "visual", "image": "visual", "shot": "visual", "prompt": "visual",
    "scene": "visual", "description": "visual", "setting": "visual", "action": "visual", "video": "visual",
    "motion": "motion", "camera": "motion", "movement": "motion", "camera move": "motion",
    "voice": "voice", "vo": "voice", "voiceover": "voice", "voice over": "voice", "voice-over": "voice",
    "narration": "voice", "narrator": "voice", "dialogue": "voice", "dialog": "voice", "line": "voice",
    "speech": "voice", "says": "voice",
    "text": "text", "on-screen text": "text", "onscreen text": "text", "on screen text": "text",
    "overlay": "text", "title card": "text", "caption": "text", "text overlay": "text", "subtitle": "text",
    "sound": "sound", "sfx": "sound", "sound effects": "sound", "sound effect": "sound", "audio": "sound",
    "ambience": "sound", "ambient": "sound", "sounds": "sound",
    "text on screen": "text", "on screen": "text", "screen text": "text", "card": "text",
    "duration": "seconds", "length": "seconds", "time": "seconds", "seconds": "seconds",
}
SCENE_FIELDS = ("visual", "motion", "voice", "text", "sound")
HEADER_ONLY = {k for k in HEADER_KEYS if k not in SCENE_KEYS}

_scene_re = re.compile(r"^(?:scene|shot|clip)\s*#?\s*(\d+)?\b(.*)$", re.I)
_chapter_re = re.compile(r"^(?:chapter|act|part)\s*#?\s*(\d+|[ivxlc]+)?\b\s*[:.\-–|]?\s*(.*)$", re.I)
_key_re = re.compile(r"^([A-Za-z][A-Za-z' \-/]{0,24}?)\s*(?:\([^)]*\))?\s*(?:[—–]\s*[^:]{0,80}?)?\s*[:=]\s*(.*)$")
_bracket_re = re.compile(r"^\[([^\]]{1,80})\]\s*(.*)$")
_slug_re = re.compile(r"^(INT|EXT|INT/EXT|I/E)[.\s]+\S", re.I)
STYLE_ADD = {"setting", "location", "world", "format", "genre", "tone", "color", "colour", "colors", "colours",
             "palette", "mood"}
NOTES_KEYS = {"director's notes", "directors notes", "director notes", "notes", "production notes",
              "director's note", "creative notes"}
IGNORE_KEYS = {"runtime", "length", "duration", "total length", "platform", "aspect ratio", "target"}
_dur_re = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b", re.I)
_range_re = re.compile(r"(\d+):(\d{2})\s*[-–]\s*(\d+):(\d{2})")


def empty():
    return {"title": "", "style": "", "characters": [], "narrator": "", "music": "", "ambience": "", "caption": "",
            "hashtags": [], "hook": "", "chapters": [], "scenes": []}


def _clean_line(line):
    s = line.strip()
    s = re.sub(r"^[#>*\-•\s]+", "", s)          # markdown headings, bullets, quotes
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"^\d+[.)]\s+", "", s) if not _scene_re.match(s) else s
    return s.strip()


def _seconds(text):
    m = _range_re.search(text)
    if m:
        a = int(m.group(1)) * 60 + int(m.group(2))
        b = int(m.group(3)) * 60 + int(m.group(4))
        if b > a:
            return float(b - a)
    m = _dur_re.search(text)
    return float(m.group(1)) if m else 0.0


def _hashtags(text):
    if isinstance(text, list):
        text = " ".join(text)
    tags = re.findall(r"#?([\wÀ-ɏ]+)", text or "")
    return ["#" + t for t in tags if t][:12]


def _character(text):
    voice = ""
    m = re.search(r"\|\s*voice\s*[:=]\s*([\w\-./]+)", text, re.I)
    if m:
        voice = m.group(1)
        text = text[:m.start()]
    if "=" in text:
        name, look = text.split("=", 1)
    elif " - " in text:
        name, look = text.split(" - ", 1)
    elif ":" in text:
        name, look = text.split(":", 1)
    elif "," in text:
        name, look = text.split(",", 1)
    else:
        name, look = text, ""
    return {"name": name.strip(" \"'"), "look": look.strip(" \"'"), "voice": voice}


def _strip_quotes(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"“'" and s[-1] in "\"”'":
        s = s[1:-1]
    return s.strip()


def parse(text):
    """Text or JSON -> script dict. Never raises; unknown lines go to the nearest field.
    Understands the factory format, markdown/bullet variants, screenplay-style scripts from web AIs
    ([0:10 - 0:50] blocks, NARRATION (V.O.), TEXT ON SCREEN, title cards, director's notes), JSON
    and plain story text."""
    text = (text or "").strip()
    js = _try_json(text)
    if js is not None:
        return from_json(js)
    sc = empty()
    cur = None          # current scene dict
    last_field = None
    in_chap = False     # between a CHAPTER line and its first SCENE
    notes = False       # inside "DIRECTOR'S NOTES": only colour/look lines are used
    loose = []          # unkeyed text before any scene (plain story text)

    def new_scene(seconds=0.0, card=False):
        nonlocal cur, last_field, in_chap, notes
        cur = {"seconds": seconds, "visual": "", "motion": "", "voice": "", "speaker": "", "text": "",
               "sound": "", "chapter": len(sc["chapters"]) - 1}
        if card:
            cur["_card"] = True
        sc["scenes"].append(cur)
        last_field, in_chap, notes = None, False, False
        return cur

    for raw in text.splitlines():
        if raw.strip().startswith("//"):        # template help lines
            continue
        line = _clean_line(raw)
        if not line:
            continue
        # ---- block markers
        cm = _chapter_re.match(line)
        if cm and (cm.group(1) or line[:7].lower() == "chapter"):
            sc["chapters"].append({"title": cm.group(2).strip(" \"'*:-–|"), "music": ""})
            in_chap, cur, last_field, notes = True, None, None, False
            continue
        bm = _bracket_re.match(line)
        if bm and _is_block(bm.group(1)):
            inner, rest = bm.group(1), bm.group(2).strip(" -–—:")
            if re.search(r"\b(title|chapter)\s*card\b|^\s*card\b", inner, re.I) and not re.search(r"\b(final|end)\b", inner, re.I):
                sc["chapters"].append({"title": "", "music": ""})       # a title card opens a chapter
                new_scene(4.0, card=True)
                cur["_opens"] = True
            elif re.search(r"\b(final|end|outro|closing)\b", inner, re.I):
                new_scene(4.0, card=True)
            else:
                new_scene(_seconds(inner) or _seconds(rest))
                if rest and sc["chapters"] and not sc["chapters"][-1]["title"] and cur["chapter"] >= 0:
                    sc["chapters"][-1]["title"] = rest.title() if rest.isupper() else rest
            continue
        if _slug_re.match(line):                                         # INT. FARMHOUSE - NIGHT
            new_scene()
            cur["visual"] = re.sub(r"^(INT|EXT|INT/EXT|I/E)[.\s]+", "", line, flags=re.I).strip(" -.")
            last_field = "visual"
            continue
        m = _scene_re.match(line)
        if m and (m.group(1) or not _key_re.match(line) or line.lower().startswith("scene ")):
            # "SCENE 1 | 3s", "Scene 2 (0:03-0:07)", "Scene: a dark room" (no number -> key form below)
            rest = m.group(2) or ""
            if not (m.group(1) is None and rest.strip().startswith(":")):
                new_scene(_seconds(rest))
                inline = re.sub(r"^[\s|:\-–]*", "", _range_re.sub("", _dur_re.sub("", rest))).strip(" |:-–()[]")
                if inline and len(inline) > 3:
                    cur["visual"] = inline
                    last_field = "visual"
                continue
        km = _key_re.match(line)
        key = re.sub(r"\s+", " ", km.group(1).strip().lower()) if km else ""
        val = km.group(2).strip() if km else line
        # ---- notes, stage directions
        if key in NOTES_KEYS or (not km and line.rstrip(":").lower() in NOTES_KEYS):
            notes, last_field = True, None
            continue
        if notes:
            if key in STYLE_ADD | {"style", "look", "visual style"}:
                sc["style"] = _join(sc["style"], val)
            continue
        if re.fullmatch(r"\(.*\)", line):
            mm = re.match(r"\(\s*music[^:]*:\s*(.+?)\.?\s*\)$", line, re.I)
            if mm and sc["chapters"] and cur is not None and cur.get("_card"):
                sc["chapters"][-1]["music"] = sc["chapters"][-1]["music"] or mm.group(1)
            continue
        if in_chap:
            ch = sc["chapters"][-1]
            if key in ("music", "soundtrack", "score", "mood"):
                ch["music"] = val
                continue
            if key in ("title", "name") and sc["title"]:
                ch["title"] = ch["title"] or val
                continue
            if not km:
                if not ch["title"]:
                    ch["title"] = line.strip(" \"'")
                continue
        if cur is not None and cur.get("_card"):
            # a title card holds only its words (+ the chapter's music)
            if SCENE_KEYS.get(key) == "text" or key in ("text on screen", "on screen", "card", "title"):
                cur["text"] = _strip_quotes(val)
                last_field = "text"
                if cur.get("_opens") and not sc["chapters"][cur["chapter"]]["title"]:
                    sc["chapters"][cur["chapter"]]["title"] = cur["text"]
            elif key in ("music", "score", "soundtrack") and cur["chapter"] >= 0:
                sc["chapters"][cur["chapter"]]["music"] = val
            elif not km and last_field == "text":
                cur["text"] = (cur["text"] + " " + _strip_quotes(line)).strip()
                if cur.get("_opens") and len(sc["chapters"][cur["chapter"]]["title"]) < len(cur["text"]) < 60:
                    sc["chapters"][cur["chapter"]]["title"] = cur["text"]
            continue
        if cur is not None and key in HEADER_ONLY:
            # a web AI repeating header lines in a later reply: keep the first value
            field = HEADER_KEYS[key]
            if field == "characters":
                c = _character(val)
                if c["name"] and not _is_speaker(c["name"].lower(), sc):
                    sc["characters"].append(c)
            elif field == "hashtags":
                sc["hashtags"] = sc["hashtags"] or _hashtags(val)
            elif not sc[field]:
                sc[field] = _strip_quotes(val)
            continue
        if cur is None:
            field = HEADER_KEYS.get(key)
            if key in STYLE_ADD:
                sc["style"] = _join(sc["style"], val)
                last_field = "style"
            elif key in ("audio", "sound", "sounds"):
                sc["ambience"] = _join(sc["ambience"], val)
                last_field = "ambience"
            elif key in IGNORE_KEYS:
                last_field = None
            elif field == "characters":
                for part in re.split(r";\s*(?=[A-Z])", val) if ";" in val else [val]:
                    if part.strip():
                        sc["characters"].append(_character(part))
                last_field = "characters"
            elif field == "hashtags":
                sc["hashtags"] = _hashtags(val)
                last_field = "hashtags"
            elif field:
                sc[field] = _strip_quotes(val) if field != "title" else val.strip(" \"'")
                last_field = field
            elif km and sc["characters"] and last_field == "characters" and key not in SCENE_KEYS:
                sc["characters"].append(_character(line))
            elif last_field in ("style", "caption", "music", "hook", "ambience"):
                sc[last_field] = (sc[last_field] + " " + line).strip()
            elif last_field == "hashtags" and "#" in line:
                sc["hashtags"] += _hashtags(line)
            elif not sc["title"] and not km and len(line.split()) <= 10:
                sc["title"] = line.strip(" \"'")
            elif not km:
                loose.append(line)
            continue
        if key in ("music", "soundtrack", "score"):
            # a music cue inside a scene: mood for this chapter (or the whole video)
            if cur["chapter"] >= 0 and not sc["chapters"][cur["chapter"]]["music"]:
                sc["chapters"][cur["chapter"]]["music"] = val
            elif not sc["music"]:
                sc["music"] = val
            last_field = None
            continue
        field = SCENE_KEYS.get(key)
        if not field and km and _is_speaker(key, sc):
            field, val = "voice", f"{km.group(1).strip()}: {val}"
        if field == "seconds":
            cur["seconds"] = _seconds(val + "s") or cur["seconds"]
            continue
        if field:
            if field == "voice":
                val = _voice_line(val, cur, sc)
            if cur[field] and field in ("visual", "sound", "motion"):
                cur[field] = cur[field].rstrip(" .,") + (", " if field != "visual" else ". ") + val
            elif cur[field] and field == "voice":
                cur[field] += " " + val
            else:
                cur[field] = val
            last_field = field
        elif last_field and last_field in cur:
            cur[last_field] = (cur[last_field] + " " + (_voice_line(line, cur, sc) if last_field == "voice" else line)).strip()
        elif not cur["visual"]:
            cur["visual"] = line
            last_field = "visual"
        else:
            cur["visual"] += ". " + line
    if not sc["scenes"] and loose:
        _from_plain(sc, loose)
    return normalize(sc)


def _is_block(inner):
    """[0:10 - 0:50], [TITLE CARD 2], [Scene 3], [FINAL CARD] are blocks; [pause], [music swells] are not."""
    return bool(re.search(r"\d+:\d{2}|\b(scene|shot|card|title|final|end|intro|outro|opening|closing|part|chapter)\b",
                          inner, re.I))


def _join(a, b):
    b = (b or "").strip().rstrip(".")
    return f"{a.rstrip('.')}. {b}" if a and b else (a or b)


def _from_plain(sc, lines):
    """Story text without scenes (e.g. a long narration from a web AI): every 1-2 sentences become a
    scene, the sentence itself is the picture prompt. 'Convert with local AI' gives better pictures."""
    text = " ".join(lines)
    sents = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", text) if x.strip()]
    cur = ""
    for x in sents:
        if cur and len((cur + " " + x).split()) > SPLIT_WORDS:
            sc["scenes"].append(_plain_scene(cur))
            cur = x
        else:
            cur = (cur + " " + x).strip()
    if cur:
        sc["scenes"].append(_plain_scene(cur))


def _plain_scene(line):
    return {"seconds": 0.0, "visual": line, "motion": "", "voice": line, "speaker": "", "text": "", "sound": "",
            "chapter": -1}


VOICE_NAME = re.compile(r"^(?:(?:fe)?male_\d+|en_(?:wo)?man|own)$", re.I)


def is_card(s):
    """A scene with only on-screen words: rendered as a title card (dark background, centred text)."""
    return bool(s.get("text")) and not s.get("visual") and not s.get("voice")


def _is_speaker(key, sc):
    return any(c["name"].lower() == key for c in sc["characters"]) or bool(VOICE_NAME.match(key or ""))


def _voice_line(val, cur, sc):
    """Pull a 'Name:' speaker prefix and stage directions out of a voice line."""
    m = re.match(r"^([A-Za-z][\w .'\-]{0,30}?)\s*(?:\([^)]*\))?\s*:\s*(.+)$", val)
    if m and (_is_speaker(m.group(1).lower(), sc) or (m.group(1)[0].isupper() and m.group(1).upper() == m.group(1))
              or (len(m.group(1).split()) <= 2 and m.group(1).istitle())) \
            and m.group(1).lower() not in ("narrator", "vo", "v.o"):
        sp = m.group(1).strip()
        cur["speaker"] = cur.get("speaker") or (sp.lower() if VOICE_NAME.match(sp) else sp.title())
        val = m.group(2)
    elif m and m.group(1).lower() in ("narrator", "vo", "v.o"):
        val = m.group(2)
    val = re.sub(r"\((?:[^)]*)\)|\[(?:[^\]]*)\]", "", val)      # (whispers) [pause]
    return _strip_quotes(re.sub(r"\s+", " ", val))


def _try_json(text):
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
        if m:
            text = m.group(1)
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def from_json(js):
    sc = empty()
    low = {str(k).lower(): v for k, v in js.items()}
    for k, v in low.items():
        f = HEADER_KEYS.get(k)
        if f == "characters" and isinstance(v, list):
            for c in v:
                if isinstance(c, dict):
                    cl = {str(a).lower(): b for a, b in c.items()}
                    sc["characters"].append({"name": str(cl.get("name", "")),
                                             "look": str(cl.get("look") or cl.get("description") or cl.get("appearance") or ""),
                                             "voice": str(cl.get("voice", "") or "")})
                elif isinstance(c, str):
                    sc["characters"].append(_character(c))
        elif f == "hashtags":
            sc["hashtags"] = _hashtags(v)
        elif f and f != "characters" and isinstance(v, (str, int, float)):
            sc[f] = str(v)
    flat = [(s, -1) for s in (low.get("scenes") or low.get("shots") or [])]
    for ci, ch in enumerate(low.get("chapters") or []):
        if isinstance(ch, dict):
            cl = {str(a).lower(): b for a, b in ch.items()}
            sc["chapters"].append({"title": str(cl.get("title") or ""), "music": str(cl.get("music") or "")})
            flat += [(s, len(sc["chapters"]) - 1) for s in cl.get("scenes") or []]
    for s, chap in flat:
        if not isinstance(s, dict):
            continue
        cur = {"seconds": 0.0, "visual": "", "motion": "", "voice": "", "speaker": "", "text": "", "sound": "",
               "chapter": int(s.get("chapter", chap)) if str(s.get("chapter", chap)).lstrip("-").isdigit() else chap}
        for k, v in s.items():
            k = str(k).lower()
            if k == "speaker":
                cur["speaker"] = str(v or "")
                continue
            if k == "chapter":
                continue
            f = SCENE_KEYS.get(k)
            if f == "seconds":
                try:
                    cur["seconds"] = float(v)
                except (TypeError, ValueError):
                    cur["seconds"] = _seconds(str(v))
            elif f:
                val = ", ".join(map(str, v)) if isinstance(v, list) else str(v or "")
                cur[f] = (cur[f] + ". " + val).strip(". ") if cur[f] else val
        cur["voice"] = _voice_line(cur["voice"], cur, sc) if cur["voice"] else ""
        sc["scenes"].append(cur)
    return normalize(sc)


def normalize(sc):
    for s in sc["scenes"]:
        s.pop("_card", None)
        s.pop("_opens", None)
        for f in SCENE_FIELDS:
            s[f] = re.sub(r"\s+", " ", str(s.get(f) or "")).strip()
        s["speaker"] = (s.get("speaker") or "").strip()
        if s["speaker"].lower() in ("narrator", "none", "vo"):
            s["speaker"] = ""
        if s["voice"].lower() in ("none", "-", "n/a", "silence", "(none)", "no voice"):
            s["voice"] = ""
        if s["text"].lower() in ("none", "-", "n/a", "(none)"):
            s["text"] = ""
        if s["sound"].lower() in ("none", "-", "n/a", "(none)"):
            s["sound"] = ""
        try:
            s["seconds"] = max(0.0, min(300.0, float(s.get("seconds") or 0)))
        except (TypeError, ValueError):
            s["seconds"] = 0.0
    sc["scenes"] = [s for s in sc["scenes"] if s["visual"] or s["voice"] or s["text"]]
    chs = [c for c in (sc.get("chapters") or []) if isinstance(c, dict)]
    for s in sc["scenes"]:
        try:
            s["chapter"] = int(s.get("chapter", -1))
        except (TypeError, ValueError):
            s["chapter"] = -1
        if s["chapter"] >= len(chs):
            s["chapter"] = len(chs) - 1
    used = sorted({s["chapter"] for s in sc["scenes"] if s["chapter"] >= 0})
    remap = {old: new for new, old in enumerate(used)}
    sc["chapters"] = [{"title": re.sub(r"\s+", " ", str(chs[i].get("title") or "")).strip(),
                       "music": re.sub(r"\s+", " ", str(chs[i].get("music") or "")).strip()} for i in used]
    for s in sc["scenes"]:
        s["chapter"] = remap.get(s["chapter"], -1)
    sc["characters"] = [c for c in sc["characters"] if c.get("name")]
    for c in sc["characters"]:
        if c["name"].isupper():
            c["name"] = c["name"].title()
    for s in sc["scenes"]:
        if s["speaker"].isupper() and not VOICE_NAME.match(s["speaker"]):
            s["speaker"] = s["speaker"].title()
    sc["hashtags"] = _hashtags(sc.get("hashtags"))
    for k in ("title", "style", "narrator", "music", "ambience", "caption", "hook"):
        sc[k] = re.sub(r"\s+", " ", str(sc.get(k) or "")).strip()
    return sc


def format_script(sc):
    out = [f"TITLE: {sc['title']}" if sc["title"] else "TITLE: Untitled"]
    if sc["style"]:
        out.append(f"STYLE: {sc['style']}")
    for c in sc["characters"]:
        out.append(f"CHARACTER: {c['name']} = {c['look']}" + (f" | voice: {c['voice']}" if c.get("voice") else ""))
    for k in ("narrator", "music", "ambience", "caption"):
        if sc[k]:
            out.append(f"{k.upper()}: {sc[k]}")
    if sc["hashtags"]:
        out.append("HASHTAGS: " + " ".join(sc["hashtags"]))
    if sc["hook"]:
        out.append(f"HOOK: {sc['hook']}")
    chap = -1
    for i, s in enumerate(sc["scenes"], 1):
        if s.get("chapter", -1) >= 0 and s["chapter"] != chap:
            chap = s["chapter"]
            c = sc["chapters"][chap]
            out += ["", f"CHAPTER {chap + 1}: {c['title']}".rstrip(": ")]
            if c.get("music"):
                out.append(f"MUSIC: {c['music']}")
        out.append("")
        out.append(f"SCENE {i}" + (f" | {s['seconds']:g}s" if s["seconds"] else ""))
        for f in SCENE_FIELDS:
            v = s[f]
            if f == "voice" and v and s.get("speaker"):
                v = f"{s['speaker']}: {v}"
            if v:
                out.append(f"{f.upper()}: {v}")
    return "\n".join(out) + "\n"


WORDS_PER_SEC = 2.6
SPLIT_WORDS = 15        # longer voice lines become several shots (exact limit follows the GPU)


def voice_seconds(text):
    return len(re.findall(r"\w+", text or "")) / WORDS_PER_SEC


def estimate(scene):
    v = voice_seconds(scene["voice"])
    return round(max(scene["seconds"] or 0, v + 0.5 if v else 0, 2.0 if v else 3.0), 1)


def check(sc):
    """Warnings a human can act on, plus the estimated length."""
    warn = []
    if not sc["scenes"]:
        warn.append("No scenes found. Start each scene with a line like 'SCENE 1 | 3s'.")
    names = {c["name"].lower() for c in sc["characters"]}
    for i, s in enumerate(sc["scenes"], 1):
        if is_card(s):
            continue
        if not s["visual"]:
            warn.append(f"Scene {i}: no VISUAL, the picture will be guessed from the voice line.")
        if len(s["text"].split()) > 8:
            warn.append(f"Scene {i}: on-screen TEXT has {len(s['text'].split())} words; keep it under 7.")
        if re.search(r"\b(sign|text|words|letters|written|reads|logo|headline)\b", s["visual"], re.I):
            warn.append(f"Scene {i}: the VISUAL asks for writing; video models draw text as gibberish. "
                        "Put words in TEXT instead.")
        if s["speaker"] and s["speaker"].lower() not in names and not VOICE_NAME.match(s["speaker"]):
            warn.append(f"Scene {i}: speaker '{s['speaker']}' has no CHARACTER line, the narrator voice is used.")
    total = sum(estimate(s) for s in sc["scenes"])
    if total > 240 and not sc["chapters"]:
        warn.append(f"About {total / 60:.0f} min long without CHAPTER lines. It works, but chapters give the "
                    "music natural changes and the viewer a title card now and then.")
    if not sc["caption"]:
        warn.append("No CAPTION yet (the TikTok description).")
    long_lines = sum(1 for s in sc["scenes"] if len(s["voice"].split()) > SPLIT_WORDS)
    return {"warnings": warn, "seconds": round(total, 1), "scenes": len(sc["scenes"]),
            "chapters": len(sc["chapters"]), "long_lines": long_lines}


def visual_prompt(sc, scene, with_motion=False):
    """Full image/video prompt: style + visual with character looks injected."""
    vis = scene["visual"] or scene["voice"] or scene["text"]
    for c in sc["characters"]:
        if c["look"] and re.search(r"\b" + re.escape(c["name"]) + r"\b", vis, re.I):
            vis = re.sub(r"\b" + re.escape(c["name"]) + r"\b", f"{c['name']} ({c['look']})", vis, count=1, flags=re.I)
    parts = [vis]
    if with_motion and scene["motion"]:
        parts.append(scene["motion"])
    if sc["style"]:
        parts.append(sc["style"])
    return ". ".join(p.strip(" .") for p in parts if p) + "."


def template(idea="", seconds=30, genre="", voices=None, narration="narrator"):
    """Prompt to paste into ChatGPT / DeepSeek; their answer pastes straight back in.
    Over 2 minutes it asks for one CHAPTER per reply (web AIs cut long answers off)."""
    voices = voices or ["female_01", "female_02", "male_01", "male_02"]
    talk = {"narrator": "Use a narrator for the VOICE lines.",
            "dialogue": "Tell it through the characters' own lines (VOICE: Name: line).",
            "mixed": "Mix narrator lines and short character lines (VOICE: Name: line).",
            "none": "No voice at all: leave VOICE empty and tell the story with pictures, TEXT and SOUND."}.get(narration, "")
    long = seconds > 120
    chapters = max(2, round(seconds / 60)) if long else 0
    per = max(6, round(seconds / chapters / 4.8)) if long else 0
    n = max(3, min(14, round(seconds / 4.5)))
    length = (f"Length about {seconds // 60} minutes: {chapters} chapters of about {per} scenes each "
              f"(scenes of 3-6 seconds)." if long else f"Length about {seconds} seconds, {n} scenes.")
    scene_block = """SCENE 1 | 4s
VISUAL: (what the camera sees: subject, action, place, light, shot type; 20-40 words)
MOTION: (one camera move: slow push in / pull back / pan left / pan right / tilt up / handheld / static)
VOICE: (narration, max 14 words) or Name: (a character's line)
TEXT: (optional on-screen words, max 6; leave empty in most scenes)
SOUND: (sound effects: wind, rain, waves, thunder, footsteps, heartbeat, clock, whoosh, impact, riser, static, fire, rumble)

SCENE 2 | 4s
..."""
    if long:
        scene_block = f"""CHAPTER 1: (chapter title)
MUSIC: (mood of the music in this chapter)

{scene_block}

(then CHAPTER 2 with its scenes, and so on; number the scenes on from the last chapter)"""
    parts = f"""
IMPORTANT, the answer is long: reply with the header lines and CHAPTER 1 only. When I write "next",
reply with the next CHAPTER only (no header again), in the same format, until all {chapters} chapters are done.
""" if long else ""
    rules_long = """- Every chapter has its own small climax and ends with a reason to keep watching.
- Keep the same characters, places and STYLE through all chapters; the narration flows on from chapter to chapter.
""" if long else ""
    return f"""Write a {"long" if long else "short"} vertical video script (TikTok / Reels / Shorts) {f'about: {idea}' if idea else '(pick a gripping idea)'}
{f'Genre / tone: {genre}. ' if genre else ''}{length} {talk}
{parts}
Reply ONLY with the script in exactly this format. No intro, no notes, no markdown, no **bold**.

TITLE: (short title)
STYLE: (one visual style used for EVERY shot: medium, lighting, colours, lens, era)
CHARACTER: Name = (fixed look: age, face, hair, clothes) | voice: {voices[0]}
NARRATOR: {voices[0]}
MUSIC: (mood, instruments, tempo)
CAPTION: (TikTok description, 1-2 sentences, end with a question)
HASHTAGS: #tag #tag #tag #tag #tag
HOOK: (first words on screen, max 6 words)

{scene_block}

Rules:
- Scene 1 grabs attention within 2 seconds (a question, a shock, or a mystery).
- One clear story with rising tension and a twist or payoff at the end.
{rules_long}- Every VISUAL stands alone (repeat the place and the character's name in each one).
- Never ask for written words, signs, screens or logos in VISUAL; words go in TEXT.
- Voices available: {', '.join(voices)}.
"""

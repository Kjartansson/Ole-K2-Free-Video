"""ffmpeg helpers: fit clips to 1080x1920, camera moves on stills, ASS subtitles, built-in sound
effects and music pads (no model needed), the final audio mix and the cover image."""
import json
import os
import random
import re
import shutil
import subprocess

import errors
from config import MUSIC_DIR, SFX_DIR
from errors import FactoryError

W, H, FPS = 1080, 1920, 30
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
LOG = None          # file object set by the pipeline; ffmpeg stderr goes there


def run(args, timeout=900):
    """Run ffmpeg. On failure the exact command and ffmpeg's output are saved to logs/fail/ and the
    FactoryError points to that file (detail_file), so a person or an agent can re-run it."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + [str(a) for a in args]
    if LOG:
        LOG.write("$ ffmpeg " + " ".join(str(a) for a in args)[:2000] + "\n")
        LOG.flush()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise FactoryError("FFMPEG", "ffmpeg is not installed", hint="Install ffmpeg (see INSTALL.md).") from None
    except subprocess.TimeoutExpired:
        detail = errors.save_detail("ffmpeg-timeout", "$ " + " ".join(cmd))
        raise FactoryError("TIMEOUT", f"ffmpeg ran longer than {timeout}s", detail_file=detail) from None
    if p.returncode != 0:
        if LOG:
            LOG.write(p.stderr[-4000:] + "\n")
        detail = errors.save_detail("ffmpeg", "COMMAND (re-run it to reproduce):\n" + " ".join(
            _shell_quote(c) for c in cmd) + f"\n\nEXIT CODE {p.returncode}\n\nFFMPEG OUTPUT:\n" + p.stderr[-12000:])
        last = (p.stderr.strip().splitlines() or ["?"])[-1][:300]
        code = "DISK_FULL" if "No space left" in p.stderr else "FFMPEG"
        raise FactoryError(code, f"ffmpeg failed: {last}", detail_file=detail,
                           output=os.path.basename(str(args[-1])) if args else "")


def _shell_quote(c):
    return c if re.fullmatch(r"[\w@%+=:,./\-]+", c) else "'" + c.replace("'", "'\\''") + "'"


def duration(path):
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                             capture_output=True, text=True, timeout=60).stdout
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def is_image(path):
    return os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ------------------------------------------------------------------ video
def _motion_kind(motion, default="in"):
    m = (motion or "").lower()
    for kind, words in (("out", ("pull back", "pull out", "zoom out", "dolly out", "reveal", "widen")),
                        ("left", ("pan left", "truck left", "move left")),
                        ("right", ("pan right", "truck right", "move right", "tracking")),
                        ("up", ("tilt up", "crane up", "rise", "pedestal up")),
                        ("down", ("tilt down", "crane down", "descend")),
                        ("static", ("static", "locked", "still shot")),
                        ("shake", ("handheld", "shaky", "shake"))):
        if any(w in m for w in words):
            return kind
    return default


LOOKS = {
    # one shared grade + fine grain makes pictures from different seeds/models sit together
    "film": ",eq=contrast=1.05:saturation=0.92:gamma=0.98,noise=alls=4:allf=t",
    "clean": "",
}
KB_CYCLE = ["in", "right", "out", "left", "up", "in", "down"]


def still_to_clip(image, dest, seconds, motion="", fade_in=0.0, fade_out=0.0, look="film", default_kind="in"):
    """Ken Burns move on a still (scaled up first so the move is smooth, not jittery)."""
    n = max(2, int(round(seconds * FPS)))
    k = _motion_kind(motion, default_kind)
    z, x, y = "1.12", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if k == "in":
        z = f"1+0.13*on/{n}"
    elif k == "out":
        z = f"1.13-0.13*on/{n}"
    elif k == "left":
        x = f"(iw-iw/zoom)*(1-on/{n})"
    elif k == "right":
        x = f"(iw-iw/zoom)*on/{n}"
    elif k == "up":
        y = f"(ih-ih/zoom)*(1-on/{n})"
    elif k == "down":
        y = f"(ih-ih/zoom)*on/{n}"
    elif k == "static":
        z = f"1.02+0.02*on/{n}"
    elif k == "shake":
        z = "1.1"
        x = "iw/2-(iw/zoom/2)+sin(on/4)*14+sin(on/9)*9"
        y = "ih/2-(ih/zoom/2)+cos(on/5)*12"
    vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase:flags=lanczos,crop={W*2}:{H*2},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},setsar=1" + LOOKS.get(look, "")
          + _fades(seconds, fade_in, fade_out))
    run(["-loop", "1", "-i", image, "-vf", vf, "-frames:v", n, "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "16", "-pix_fmt", "yuv420p", dest])
    return dest


def fit_clip(src, dest, seconds, fade_in=0.0, fade_out=0.0, max_slow=1.5, look="film"):
    """Any video -> exactly `seconds` at 1080x1920/30fps: slow down a little, then hold the last frame."""
    have = duration(src) or seconds
    slow = min(max_slow, seconds / have) if have < seconds else 1.0
    pad = max(0.0, seconds - have * slow) + 0.2
    vf = (f"setpts=PTS*{slow:.4f},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={W}:{H},fps={FPS},setsar=1,tpad=stop_mode=clone:stop_duration={pad:.3f},"
          f"trim=duration={seconds:.3f},setpts=PTS-STARTPTS" + LOOKS.get(look, "") + _fades(seconds, fade_in, fade_out))
    run(["-i", src, "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", dest])
    return dest


def _fades(seconds, fade_in, fade_out):
    f = ""
    if fade_in:
        f += f",fade=t=in:st=0:d={fade_in}"
    if fade_out:
        f += f",fade=t=out:st={max(0, seconds - fade_out):.3f}:d={fade_out}"
    return f


def last_frame(video, dest):
    run(["-sseof", "-0.25", "-i", video, "-update", "1", "-frames:v", 50, "-q:v", 2, dest])
    return dest


def join_segments(segs, dest):
    """Join LTX segments made from each other's last frame (drop the repeated first frame)."""
    ins, parts = [], []
    for i, sp in enumerate(segs):
        ins += ["-i", sp]
        parts.append(f"[{i}:v]" + ("trim=start_frame=1," if i else "") + f"setpts=PTS-STARTPTS,fps={FPS}[s{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[s{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[v]"
    run(ins + ["-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", dest])
    return dest


def _xfade_chain(n, shown, overlap, kinds):
    """Filter text that crossfades inputs 0..n-1. shown[i] = seconds input i is on screen before the
    next one starts fading in; every input but the last is `overlap` longer than shown[i]."""
    g = [f"[{i}:v]settb=AVTB,fps={FPS},format=yuv420p,setsar=1[a{i}]" for i in range(n)]
    prev, t = "a0", 0.0
    for j in range(1, n):
        t += shown[j - 1]
        g.append(f"[{prev}][a{j}]xfade=transition={kinds[j - 1]}:duration={overlap:.3f}:offset={t:.3f}[x{j}]")
        prev = f"x{j}"
    return ";".join(g), prev


def _overlay_graph(cur, overlays, first):
    """Logo overlay chain on top of pad `cur` (no brackets); logo inputs start at index `first`
    (extra -i inputs AFTER the audio input). Returns (graph, last pad); ("", cur) with no overlays."""
    graph = []
    for k, o in enumerate(overlays):
        graph.append(f"[{first + k}:v]scale=-1:{o['h']},format=rgba,colorchannelmixer=aa={o['opacity']}[lg{k}]")
        graph.append(f"[{cur}][lg{k}]overlay=x={o['x']}:y={o['y']}"
                     f":enable='between(t,{o['start']},{o['end']})'[ov{k}]")
        cur = f"ov{k}"
    return ";".join(graph), cur


def assemble(clips, durs, dest, workdir, audio, ass=None, transition="crossfade", overlap=0.35,
             chapter_starts=(), crf=20, chunk=12, on_step=None, overlays=()):
    """Clips -> final.mp4 in ONE final encode (subtitles burned in, audio muxed).
    crossfade: every clip except the last must be `overlap` s longer than its scene (durs).
    Long videos crossfade in chunks of `chunk` clips first (ffmpeg should not open 150 inputs).
    overlays: [{"path", "x", "y", "h", "start", "end", "opacity"}] logo overlays burned in before
    the subtitles (x/y may be ffmpeg overlay expressions like "(W-w)/2")."""
    total = sum(durs)
    sub = ""
    if ass:
        esc = ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        sub = f",ass='{esc}'"
    # bitrate cap: grain makes x264 spend a lot; TikTok's app refuses very large files (a 4 min video was 309 MB)
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", crf, "-maxrate", "6M", "-bufsize", "12M",
           "-pix_fmt", "yuv420p", "-r", FPS,
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total:.3f}", dest]
    logos = sum((["-i", o["path"]] for o in overlays), [])
    n = len(clips)
    if transition != "crossfade" or n == 1:
        lst = os.path.join(workdir, "concat.txt")
        with open(lst, "w") as f:
            for c in clips:
                f.write("file '" + os.path.abspath(c).replace("'", "'\\''") + "'\n")
        ograph, olast = _overlay_graph("0:v", overlays, 2)
        fc = (ograph + ";" if ograph else "") + f"[{olast}]null{sub}[v]"
        run(["-f", "concat", "-safe", "0", "-i", lst, "-i", audio] + logos +
            ["-filter_complex", fc, "-map", "[v]", "-map", "1:a"] + enc, timeout=7200)
        return dest
    starts = set(chapter_starts)
    kinds = ["fadeblack" if (i + 1) in starts else "fade" for i in range(n)]
    groups = [list(range(i, min(n, i + chunk))) for i in range(0, n, chunk)]
    if len(groups) == 1:
        inputs, shown, gk = clips, durs, kinds
    else:
        inputs, shown, gk = [], [], []
        for gi, idx in enumerate(groups):
            out = os.path.join(workdir, f"chunk{gi:02d}.mp4")
            ins = sum((["-i", clips[i]] for i in idx), [])
            if len(idx) == 1:
                graph, last = f"[0:v]settb=AVTB,fps={FPS},format=yuv420p,setsar=1[x0]", "x0"
            else:
                graph, last = _xfade_chain(len(idx), [durs[i] for i in idx], overlap, [kinds[i] for i in idx])
            run(ins + ["-filter_complex", graph, "-map", f"[{last}]", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "16", "-pix_fmt", "yuv420p", out], timeout=3600)
            inputs.append(out)
            shown.append(sum(durs[i] for i in idx))
            gk.append(kinds[idx[-1]])
            if on_step:
                on_step(gi + 1, len(groups) + 1)
    graph, last = _xfade_chain(len(inputs), shown, overlap, gk)
    ins = sum((["-i", c] for c in inputs), [])
    ograph, olast = _overlay_graph(last, overlays, len(inputs) + 1)
    fc = graph + (";" + ograph if ograph else "") + f";[{olast}]null{sub}[v]"
    run(ins + ["-i", audio] + logos + ["-filter_complex", fc, "-map", "[v]",
               "-map", f"{len(inputs)}:a"] + enc, timeout=7200)
    return dest


def concat(clips, dest, workdir):
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w") as f:
        for c in clips:
            f.write("file '" + os.path.abspath(c).replace("'", "'\\''") + "'\n")
    run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dest])
    return dest


# ------------------------------------------------------------------ subtitles (ASS)
FONT_CHOICES = ["Montserrat ExtraBold", "Montserrat", "Anton", "Noto Sans Black", "Lato Black", "Arial Black",
                "Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial"]


SERIF_CHOICES = ["Noto Serif Display", "Noto Serif", "DejaVu Serif", "Liberation Serif", "Georgia", "Times New Roman"]


def pick_serif():
    try:
        fams = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout
        have = {f.strip() for line in fams.splitlines() for f in line.split(",")}
        return next((f for f in SERIF_CHOICES if f in have), "Times New Roman")
    except Exception:
        return "Times New Roman"


def card_clip(dest, seconds, look="film", fade_in=0.0, fade_out=0.0):
    """Title card background: near-black with the same grain as the film (the words are subtitles)."""
    vf = "format=yuv420p" + LOOKS.get(look, "") + _fades(seconds, fade_in, fade_out)
    run(["-f", "lavfi", "-i", f"color=c=0x080706:s={W}x{H}:r={FPS}:d={seconds:.3f}", "-vf", vf, "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", dest])
    return dest


def pick_font(wanted=""):
    if wanted:
        return wanted
    try:
        fams = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout
        have = {f.strip() for line in fams.splitlines() for f in line.split(",")}
        for f in FONT_CHOICES:
            if f in have:
                return f
    except Exception:
        pass
    return "Arial"


def _t(sec):
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    return f"{h}:{m:02d}:{sec % 60:05.2f}"


def _esc(s):
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def _hex6(hexrgb):
    """'#abc' / '#AABBCC' -> 'AABBCC'; anything else -> the default brand colour (never white on white)."""
    h = (hexrgb or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", h):
        h = "".join(c * 2 for c in h)
    return h if re.fullmatch(r"[0-9a-fA-F]{6}", h) else "FFB020"


def ass_color(hexrgb, alpha=0.0):
    """Hex #RRGGBB -> ASS &HAABBGGRR (alpha 0 = opaque, 1 = invisible)."""
    h = _hex6(hexrgb)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = max(0, min(255, int(round(alpha * 255))))
    return "&H%02X%02X%02X%02X" % (a, b, g, r)


def text_on(hexrgb):
    """Readable text colour on a #RRGGBB background: black on light, white on dark."""
    h = _hex6(hexrgb)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return "&H00000000" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 else "&H00FFFFFF"


def image_size(path):
    """(w, h) of a picture via ffprobe, (0, 0) on failure."""
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=width,height",
                              "-of", "json", path], capture_output=True, text=True, timeout=60).stdout
        st = json.loads(out)["streams"][0]
        return int(st["width"]), int(st["height"])
    except Exception:
        return 0, 0


def write_ass(path, cues, font, serif="", brand=None):
    """cues: [{kind: 'big'|'cap'|'card'|'cover'|'brand'|'banner'|'endcard', start, end, text, ...}]
    brand = {"color", "opacity", "position"} adds the Brand/Banner/Endcard styles."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},76,&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,90,150,600,1
Style: Big,{font},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,1,0,1,7,3,8,80,80,330,1
Style: Card,{serif or font},84,&H00F2F2F2,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,0,0,5,120,120,0,1
Style: Cover,{font},128,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,1,0,1,9,4,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    if brand:
        col = brand.get("color", "#FFB020")
        wa = 1 - float(brand.get("opacity", 0.8))
        al, ml, mr, mv = {"top-left": (7, 60, 60, 170), "top-center": (8, 0, 0, 170),
                          "top-right": (9, 60, 150, 170), "bottom-left": (1, 60, 60, 470)}.get(
            brand.get("position", "top-left"), (7, 60, 60, 170))
        styles = "\n".join([
            f"Style: Brand,{font},46,{ass_color('#FFFFFF', wa)},&H00FFFFFF,{ass_color('#000000', wa)},&H64000000,-1,0,0,0,100,100,0,0,1,3,1,{al},{ml},{mr},{mv},1",
            f"Style: Banner,{font},52,{text_on(col)},&H00FFFFFF,{ass_color(col)},{ass_color(col)},-1,0,0,0,100,100,0,0,3,16,0,8,80,150,250,1",
            f"Style: Endcard,{font},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,0,0,5,80,80,80,1"])
        head = head.replace("\n\n[Events]", "\n" + styles + "\n\n[Events]")
    lines = []
    for c in cues:
        if c["kind"] == "big":
            lines.append(f"Dialogue: 1,{_t(c['start'])},{_t(c['end'])},Big,,0,0,0,,{{\\fad(150,200)}}{_esc(c['text'].upper())}")
        elif c["kind"] == "card":
            body = "\\N".join(_esc(x.strip()) for x in re.split(r"(?<=[.!?])\s+", c["text"]) if x.strip())
            lines.append(f"Dialogue: 2,{_t(c['start'])},{_t(c['end'])},Card,,0,0,0,,{{\\fad(600,600)}}{body}")
        elif c["kind"] == "cover":
            lines.append(f"Dialogue: 1,{_t(c['start'])},{_t(c['end'])},Cover,,0,0,0,,{_esc(c['text'].upper())}")
        elif c["kind"] == "brand":
            lines.append(f"Dialogue: 3,{_t(c['start'])},{_t(c['end'])},Brand,,{int(c.get('margin_l') or 0)},0,0,,{_esc(c['text'])}")
        elif c["kind"] == "banner":
            lines.append(f"Dialogue: 3,{_t(c['start'])},{_t(c['end'])},Banner,,0,0,0,,{{\\fad(300,300)}}{_esc(c['text'])}")
        elif c["kind"] == "endcard":
            body = "{\\fad(400,0)}" + ("{\\pos(540,1150)}" if c.get("logo") else "")
            if c.get("line"):
                body += "{\\fs48\\b0}" + _esc(c["line"]) + "\\N{\\fs92\\b1}"
            body += _esc(c["text"])
            lines.append(f"Dialogue: 3,{_t(c['start'])},{_t(c['end'])},Endcard,,0,0,0,,{body}")
        else:
            # karaoke: the spoken word turns yellow (PrimaryColour) as it is said
            body = "".join(f"{{\\k{max(1, int(round(d * 100)))}}}{_esc(w)} " for w, d in c["words"]).strip()
            lines.append(f"Dialogue: 0,{_t(c['start'])},{_t(c['end'])},Cap,,0,0,0,,{body}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")
    return path


def caption_cues(text, start, dur, max_words=4):
    """Split a voice line into 2-4 word chunks timed by word length (no speech recognition needed)."""
    words = text.split()
    if not words or dur <= 0:
        return []
    weight = [len(re.sub(r"\W", "", w)) + 2 + (3 if re.search(r"[,.!?;:]$", w) else 0) for w in words]
    unit = dur / sum(weight)
    chunks, cur = [], []
    for w, wt in zip(words, weight):
        cur.append((w, wt * unit))
        if len(cur) >= max_words or re.search(r"[.!?]$", w):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    cues, t = [], start
    for ch in chunks:
        d = sum(x[1] for x in ch)
        cues.append({"kind": "cap", "start": t, "end": t + d + 0.05, "words": ch, "text": ""})
        t += d
    return cues


def burn_subs(video, ass, dest):
    esc = ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    run(["-i", video, "-vf", f"ass='{esc}'", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", dest])
    return dest


# ------------------------------------------------------------------ sound effects
# Built-in effects synthesised by ffmpeg (no model, no download). Your own files in sfx/
# win when their file name contains the word (e.g. sfx/door-creak.wav for "door creak").
BUILTIN_SFX = {
    "wind": ("anoisesrc=d=12:c=pink:a=0.6:r=48000,lowpass=f=700,highpass=f=90,tremolo=f=0.18:d=0.7,volume=1.4", 12),
    "rain": ("anoisesrc=d=12:c=white:a=0.25:r=48000,highpass=f=1200,lowpass=f=8000,volume=0.9", 12),
    "waves": ("anoisesrc=d=12:c=brown:a=0.8:r=48000,lowpass=f=1100,tremolo=f=0.13:d=0.9,volume=1.6", 12),
    "rumble": ("anoisesrc=d=10:c=brown:a=0.9:r=48000,lowpass=f=140,volume=1.4", 10),
    "thunder": ("anoisesrc=d=5:c=brown:a=1:r=48000,lowpass=f=320,afade=t=in:d=0.04,afade=t=out:st=0.4:d=4.4,volume=1.8", 5),
    "whoosh": ("anoisesrc=d=1.3:c=pink:a=0.9:r=48000,highpass=f=350,lowpass=f=3500,afade=t=in:d=0.65:curve=exp,afade=t=out:st=0.65:d=0.65:curve=exp,volume=2", 1.3),
    "impact": ("aevalsrc='0.9*sin(2*PI*48*t)*exp(-3.5*t)+0.5*sin(2*PI*96*t)*exp(-6*t)':d=2.5:s=48000,volume=1.1", 2.5),
    "heartbeat": ("aevalsrc='0.9*sin(2*PI*52*t)*exp(-14*mod(t,1))+0.7*sin(2*PI*46*t)*exp(-14*mod(t-0.3,1))*gt(mod(t,1),0.3)':d=10:s=48000,lowpass=f=200,volume=1.3", 10),
    "clock": ("aevalsrc='0.6*sin(2*PI*2400*t)*exp(-300*mod(t,0.5))':d=10:s=48000", 10),
    "footsteps": ("aevalsrc='0.9*sin(2*PI*85*t)*exp(-35*mod(t,0.55))':d=10:s=48000[a];anoisesrc=d=10:c=brown:a=0.05:r=48000[b];[a][b]amix=inputs=2", 10),
    "riser": ("aevalsrc='0.35*sin(2*PI*(90*t+40*t*t))+0.2*sin(2*PI*(180*t+80*t*t))':d=4:s=48000,afade=t=in:d=3.6:curve=exp,afade=t=out:st=3.8:d=0.2", 4),
    "static": ("anoisesrc=d=8:c=white:a=0.5:r=48000,highpass=f=1500,lowpass=f=6000,tremolo=f=7:d=0.85,volume=2", 8),
    "hum": ("aevalsrc='0.25*sin(2*PI*60*t)+0.1*sin(2*PI*120*t)':d=10:s=48000[a];anoisesrc=d=10:c=brown:a=0.05:r=48000[b];[a][b]amix=inputs=2", 10),
    "fire": ("anoisesrc=d=10:c=brown:a=0.5:r=48000,lowpass=f=900[a];aevalsrc='0.8*gt(random(0),0.9994)':d=10:s=48000[b];[a][b]amix=inputs=2,volume=1.6", 10),
}
SFX_WORDS = {
    "wind": ["wind", "storm", "breeze", "gust", "howl", "blizzard"],
    "rain": ["rain", "drizzle", "downpour", "shower"],
    "waves": ["wave", "ocean", "sea", "surf", "shore", "beach", "water"],
    "rumble": ["rumble", "drone", "earthquake", "engine", "train", "tremor", "ambient", "ambience"],
    "thunder": ["thunder", "lightning"],
    "whoosh": ["whoosh", "swoosh", "swish", "transition", "fly", "swipe"],
    "impact": ["impact", "boom", "hit", "slam", "crash", "bang", "explosion", "punch", "thud", "sting"],
    "heartbeat": ["heart", "pulse", "heartbeat"],
    "clock": ["clock", "tick", "timer", "watch"],
    "footsteps": ["footstep", "steps", "walk", "running", "knock"],
    "riser": ["riser", "rising", "tension", "build", "suspense", "swell"],
    "static": ["static", "glitch", "radio", "tv", "electric", "signal", "interference"],
    "hum": ["hum", "buzz", "neon", "fluorescent", "machine", "fridge"],
    "fire": ["fire", "crackl", "crack of", "first real crack", "flame", "campfire", "burn", "hearth", "fireplace",
             "embers", "ignition", "log "],
}


def builtin_sfx(name):
    d = os.path.join(SFX_DIR, "builtin")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name + ".wav")
    if not os.path.exists(path):
        graph, dur = BUILTIN_SFX[name]
        run(["-filter_complex", graph + ",alimiter=limit=0.89:level=false", "-t", dur, "-ac", "1", "-ar", "48000", path])
    return path


def user_sfx():
    out = {}
    for root, _, files in os.walk(SFX_DIR):
        if os.path.basename(root) == "builtin":
            continue
        for f in files:
            if os.path.splitext(f)[1].lower() in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
                out[os.path.join(root, f)] = set(re.findall(r"[a-z]+", f.lower()))
    return out


def find_sfx(sound_text, limit=2):
    """Sound effect files for a SOUND line: your own files first, then built-in ones."""
    txt = (sound_text or "").lower()
    if not txt:
        return []
    found = []
    words = set(re.findall(r"[a-z]+", txt))
    for path, fw in user_sfx().items():
        if fw & words - {"sound", "sounds", "the", "and", "of", "a", "in", "effect", "wav", "mp3"}:
            found.append(path)
    for name, keys in SFX_WORDS.items():
        if len(found) >= limit:
            break
        if any(k in txt for k in keys):
            try:
                found.append(builtin_sfx(name))
            except Exception:
                pass
    return found[:limit]


def sfx_track(path, dest, seconds, volume=0.35):
    """Loop/trim one effect to the scene length with soft edges."""
    fo = min(0.4, seconds / 3)
    run(["-stream_loop", "-1", "-i", path, "-t", f"{seconds:.3f}", "-af",
         f"aresample=48000,aformat=channel_layouts=mono,afade=t=in:d=0.08,afade=t=out:st={seconds - fo:.3f}:d={fo:.3f},"
         f"volume={volume}", "-ac", "1", "-ar", "48000", dest])
    return dest


# ------------------------------------------------------------------ music
CHORDS = {   # Hz, 4 chords x 3 notes, each chord 4 s
    "calm": [(130.81, 164.81, 246.94), (110.0, 130.81, 196.0), (87.31, 130.81, 164.81), (98.0, 146.83, 246.94)],
    "dark": [(110.0, 130.81, 164.81), (87.31, 110.0, 130.81), (73.42, 87.31, 110.0), (82.41, 103.83, 123.47)],
    "tense": [(110.0, 116.54, 164.81), (110.0, 116.54, 155.56), (103.83, 110.0, 164.81), (110.0, 123.47, 174.61)],
    "uplifting": [(261.63, 329.63, 392.0), (196.0, 246.94, 293.66), (220.0, 261.63, 329.63), (174.61, 220.0, 261.63)],
}
MOOD_WORDS = {
    "dark": ["dark", "horror", "ominous", "eerie", "sinister", "sad", "melanchol", "creepy", "haunt", "mystery", "mysterious", "noir", "grim"],
    "tense": ["tense", "suspense", "thriller", "action", "urgent", "pulse", "pulsing", "chase", "frantic", "danger", "anxious"],
    "uplifting": ["happy", "upbeat", "uplift", "inspir", "motivat", "hopeful", "bright", "epic", "triumph", "joy", "fun", "energetic"],
}


def mood_of(text):
    t = (text or "").lower()
    for mood, words in MOOD_WORDS.items():
        if any(w in t for w in words):
            return mood
    return "calm"


def builtin_music(mood, seconds, dest, shift=0):
    """A soft synth pad that follows a 4-chord loop (fades between chords, no clicks).
    shift = semitones up/down, so long videos can change key per chapter."""
    k = 2 ** (shift / 12)
    ch = [tuple(round(f * k, 2) for f in c) for c in CHORDS.get(mood, CHORDS["calm"])]

    def freq(note, detune):
        f = [c[note] + detune for c in ch]
        return f"if(lt(mod(t,16),4),{f[0]},if(lt(mod(t,16),8),{f[1]},if(lt(mod(t,16),12),{f[2]},{f[3]})))"

    env = "pow(sin(PI*mod(t,4)/4),0.6)"
    if mood == "tense":
        env += "*(0.65+0.35*sin(2*PI*2*t))"
    bass = f"0.18*sin(2*PI*({freq(0, 0)})/2*t)"

    def chan(det):
        tones = "+".join(f"0.14*sin(2*PI*({freq(i, det)})*t)+0.05*sin(2*PI*2*({freq(i, det)})*t)" for i in range(3))
        return f"({tones}+{bass})*{env}"

    expr = f"{chan(0)}|{chan(0.4)}"
    total = seconds + 1
    run(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={total:.2f}",
         "-af", f"lowpass=f={'2600' if mood == 'uplifting' else '1500'},aecho=0.8:0.7:120|260:0.35|0.22,"
                f"afade=t=in:d=1.5,afade=t=out:st={max(0, seconds - 2.5):.2f}:d=2.5,volume=1.6",
         "-t", f"{seconds:.3f}", "-ar", "48000", dest])
    return dest


def join_music(parts, dest, total, xf=3.0):
    """Crossfade music parts (each but the last xf seconds longer than its section)."""
    if len(parts) == 1:
        return fit_music(parts[0], total, dest)
    ins = sum((["-i", p] for p in parts), [])
    g = [f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[p{i}]" for i in range(len(parts))]
    prev = "p0"
    for j in range(1, len(parts)):
        g.append(f"[{prev}][p{j}]acrossfade=d={xf}:c1=tri:c2=tri[m{j}]")
        prev = f"m{j}"
    g.append(f"[{prev}]atrim=duration={total:.3f},afade=t=out:st={max(0, total - 3):.2f}:d=3[out]")
    run(ins + ["-filter_complex", ";".join(g), "-map", "[out]", "-ar", "48000", dest])
    return dest


def music_files():
    return sorted(f for f in os.listdir(MUSIC_DIR)
                  if os.path.splitext(f)[1].lower() in (".mp3", ".wav", ".ogg", ".flac", ".m4a"))


def fit_music(src, seconds, dest):
    run(["-stream_loop", "-1", "-i", src, "-t", f"{seconds:.3f}", "-af",
         f"aresample=48000,afade=t=in:d=1,afade=t=out:st={max(0, seconds - 2.5):.2f}:d=2.5", "-ac", "2", dest])
    return dest


def file_music(name, seconds, dest):
    files = music_files()
    if not files:
        raise FactoryError("BAD_REQUEST", "No music files in the music/ folder",
                           hint="Add music on Settings > Sound and text, or pick the built-in music.")
    pick = name if name in files else random.choice(files)
    run(["-stream_loop", "-1", "-i", os.path.join(MUSIC_DIR, pick), "-t", f"{seconds:.3f}", "-af",
         f"aresample=48000,afade=t=in:d=1,afade=t=out:st={max(0, seconds - 2.5):.2f}:d=2.5", "-ac", "2", dest])
    return dest


# ------------------------------------------------------------------ final mix
def prepare_voice(src, dest, speed=1.0):
    """48 kHz mono, silence trimmed at both ends, optional speed change (pitch kept)."""
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
            "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse")
    af = f"aresample=48000,{trim}"
    if abs(speed - 1.0) > 0.01:
        af += f",atempo={max(0.5, min(2.0, speed)):.3f}"
    run(["-i", src, "-af", af, "-ac", "1", "-ar", "48000", dest])
    return dest


def mix(total, voices, sfx, music, dest, music_volume=0.22):
    """voices/sfx: [(path, start_sec)]; music: path or None. Ducks music under the voice and
    normalises to -14 LUFS (TikTok loudness)."""
    inputs, graph, labels = [], [], {"v": [], "s": []}
    for kind, items in (("v", voices), ("s", sfx)):
        for path, start in items:
            idx = len(inputs) // 2
            inputs += ["-i", path]
            ms = int(start * 1000)
            graph.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[{kind}{idx}]")
            labels[kind].append(f"[{kind}{idx}]")
    silent = f"anullsrc=r=48000:cl=stereo,atrim=duration={total:.3f}"
    graph.append(f"{silent}[base]")
    vlist = labels["v"] or []
    graph.append(f"[base]{''.join(vlist)}amix=inputs={1 + len(vlist)}:normalize=0:duration=first[voice]")
    graph.append("[voice]asplit=2[voice1][key]")
    slist = labels["s"]
    if slist:
        graph.append(f"{silent}[sbase];[sbase]{''.join(slist)}amix=inputs={1 + len(slist)}:normalize=0:duration=first[sfx]")
    if music:
        idx = len(inputs) // 2
        inputs += ["-i", music]
        graph.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={total:.3f},"
                     f"volume={music_volume}[mus]")
        graph.append("[mus][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=450:makeup=1[duck]")
    else:
        graph.append("[key]anullsink")
    parts = ["[voice1]"] + (["[sfx]"] if slist else []) + (["[duck]"] if music else [])
    graph.append(f"{''.join(parts)}amix=inputs={len(parts)}:normalize=0:duration=first,"
                 f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[out]")
    run(inputs + ["-filter_complex", ";".join(graph), "-map", "[out]", "-t", f"{total:.3f}",
                  "-c:a", "pcm_s16le", dest])
    return dest


def mux(video, audio, dest, crf=20):
    run(["-i", video, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium",
         "-crf", crf, "-pix_fmt", "yuv420p", "-r", FPS, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", "-shortest", dest])
    return dest


def cover(video, at, title, dest, workdir, font, extra_cues=(), brand=None):
    """Cover image: a frame from the video with the hook/title printed big in the middle."""
    frame = os.path.join(workdir, "cover_frame.png")
    run(["-ss", f"{at:.2f}", "-i", video, "-frames:v", 1, frame])
    cues = ([{"kind": "cover", "start": 0, "end": 5, "text": title}] if title else []) + list(extra_cues)
    ass = write_ass(os.path.join(workdir, "cover.ass"), cues, font, brand=brand)
    esc = ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    run(["-i", frame, "-vf", f"eq=brightness=-0.06,ass='{esc}'", "-frames:v", 1, "-q:v", 2, dest])
    return dest

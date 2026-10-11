"""Part A test (no GPU): branding styles, cues and logo overlays on a 4 s test video."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "app"))
import config  # noqa: E402
import media   # noqa: E402

out = os.path.join(config.LOGS_DIR, "tests", "branding_a")
os.makedirs(out, exist_ok=True)
logo = os.path.join(out, "logo.png")
media.run(["-f", "lavfi", "-i", "color=c=0x2266FF:s=300x120", "-frames:v", 1, logo])
print("ass_color #FFB020 ->", media.ass_color("#FFB020"), "| text_on ->", media.text_on("#FFB020"),
      "| image_size ->", media.image_size(logo))

clips = []
for i in range(2):
    c = os.path.join(out, f"c{i}.mp4")
    media.card_clip(c, 2.35 if i == 0 else 2.0, "film")
    clips.append(c)
audio = os.path.join(out, "a.wav")
media.run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", 4, audio])
brand = {"color": "#FFB020", "opacity": 0.8, "position": "top-left"}
cues = [{"kind": "brand", "start": 0, "end": 2.0, "text": "voomreel.com", "margin_l": 60 + 160 + 16},
        {"kind": "banner", "start": 0.3, "end": 2.0, "text": "Free video downloader → voomreel.com"},
        {"kind": "endcard", "start": 2.1, "end": 4.0, "text": "voomreel.com", "line": "Download any video at",
         "logo": True}]
ass = media.write_ass(os.path.join(out, "subs.ass"), cues, media.pick_font(), media.pick_serif(), brand=brand)
ovs = [{"path": logo, "x": 60, "y": 160, "h": 64, "start": 0, "end": 2.0, "opacity": 0.8},
       {"path": logo, "x": "(W-w)/2", "y": 640, "h": 240, "start": 2.2, "end": 4.0, "opacity": 1.0}]
for mode in ("crossfade", "cut"):
    dest = os.path.join(out, f"final_{mode}.mp4")
    media.assemble(clips, [2.0, 2.0], dest, out, audio, ass, transition=mode, overlap=0.35, overlays=ovs)
    print(mode, "->", round(media.duration(dest), 2), "s")
for t, name in ((1.0, "f1"), (3.2, "f2")):
    media.run(["-ss", t, "-i", os.path.join(out, "final_crossfade.mp4"), "-frames:v", 1,
               os.path.join(out, name + ".png")])
media.cover(clips[0], 0.5, "HOOK TEXT", os.path.join(out, "cover.jpg"), out, media.pick_font(),
            extra_cues=[{"kind": "brand", "start": 0, "end": 5, "text": "voomreel.com"}], brand=brand)
# no overlays and no brand: the filter must be unchanged (old call shape still works)
media.assemble(clips, [2.0, 2.0], os.path.join(out, "plain.mp4"), out, audio, None, transition="crossfade")
print("frames:", sorted(f for f in os.listdir(out) if f.endswith((".png", ".jpg"))))
print("PART A OK")

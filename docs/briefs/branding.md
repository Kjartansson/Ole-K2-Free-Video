# Brief: branding for marketing (watermark URL, banner, end card)

Ole (2026-10-11): "for marketing folks that will generate videos with this, make it possible to add a
url watermark that will show the users to go to a url like voomreel.com, or a banner".
Everything is OFF while `brand_text` and `banner_text` are empty. Stdlib Python + ffmpeg/ASS only.
Rules for the agent: do NOT commit, do NOT touch files other than the ones named in your part, no
narration, follow the code style around you (see CLAUDE.md), raise `errors.FactoryError` not
RuntimeError. Video is 1080x1920; TikTok covers the right edge (~150 px, buttons) and the bottom
~470 px (caption), so branding stays out of those.

## Part A: config.py + media.py (no GPU)

1. `app/config.py`: after the line `"chapter_titles": True, ...` (line ~72) in DEFAULTS add:
```python
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
```
   and `BRAND_DIR = os.path.join(ROOT, "brand")` next to MUSIC_DIR, added to the `os.makedirs` loop.

2. `app/media.py`:
   - helpers near write_ass:
     `ass_color(hexrgb, alpha=0.0)` -> ASS `&HAABBGGRR` (alpha 0 = opaque, 1 = invisible);
     `text_on(hexrgb)` -> `&H00000000` (black) if the colour's luminance > 0.55 else `&H00FFFFFF`;
     `image_size(path)` -> (w, h) via ffprobe (`-show_entries stream=width,height`), (0, 0) on failure.
   - `write_ass(path, cues, font, serif="", brand=None)`; brand = {"color", "opacity", "position"}.
     Add 3 styles (same Format line as the others) when brand is given:
     * `Brand`: font, size 46, primary white with alpha (1 - opacity), outline black with the same alpha,
       bold, BorderStyle 1, Outline 3, Shadow 1. Alignment/margins from position:
       top-left -> 7, MarginL 60, MarginV 170; top-center -> 8, MarginV 170; top-right -> 9, MarginR 150,
       MarginV 170; bottom-left -> 1, MarginL 60, MarginV 470.
     * `Banner`: font, size 52, primary = text_on(color), OutlineColour = BackColour = ass_color(color),
       bold, BorderStyle 3 (opaque box), Outline 16, Shadow 0, Alignment 8, MarginL 80, MarginR 150,
       MarginV 250.
     * `Endcard`: font, size 92, white, bold, BorderStyle 1, Outline 0, Shadow 0, Alignment 5, margins 80.
     New cue kinds in the Dialogue loop (layer 3):
     * `brand`: text as is; if `c.get("margin_l")` put it in the Dialogue MarginL field (else 0).
     * `banner`: `{\fad(300,300)}` + text.
     * `endcard`: `{\fad(400,0)}`, plus `{\pos(540,1150)}` when `c.get("logo")`; text = brand; when
       `c.get("line")`: `{\fs48\b0}` + line + `\N{\fs92\b1}` + brand.
   - `assemble(..., overlays=())`: overlays = list of {"path", "x", "y", "h", "start", "end", "opacity"}
     (x/y may be ffmpeg overlay expressions like "(W-w)/2"). Logos are extra `-i <path>` inputs AFTER the
     audio input. Before the subtitles, chain for each overlay k:
     `[<idx>:v]scale=-1:<h>,format=rgba,colorchannelmixer=aa=<opacity>[lg<k>]` and
     `[<cur>][lg<k>]overlay=x=<x>:y=<y>:enable='between(t,<start>,<end>)'[ov<k>]`, then the existing
     `null{sub}[v]` on the last label. Both paths (concat and crossfade) must support it. With no overlays
     the output must be byte-for-byte the same filter as now.
   - `cover(video, at, title, dest, workdir, font, extra_cues=(), brand=None)`: extra_cues appended to the
     cover's cues, brand passed to write_ass.

   Test (run exactly this, it needs no GPU): `timeout 120 python3 docs/briefs/test_branding_a.py 2>&1 | tail -15`

## Part B: pipeline.py (run only when no render is running: the test re-renders a cached project)

Exact places (line numbers from 2026-10-11 22:15, check them):
1. Right BEFORE `todo = [sh for sh in shots if not only_scene ...]` (~line 352):
```python
    brand_on = o.get("branding", "on") != "off" and not only_scene
    brand_text = (o.get("brand_text") or "").strip() if brand_on else ""
    if brand_text and o.get("end_card", True) and shots:
        shots.append({"id": "send", "scene": n_scenes + 1, "part": 0, "parts": 1, "card": True,
                      "engine": "card", "endcard": True, "text": brand_text, "visual": "", "voice": "",
                      "speaker": "", "sound": "", "motion": "", "seconds": 3.0,
                      "chapter": shots[-1]["chapter"], "seed": 0})
```
2. In the cue loop (`for j, sh in enumerate(shots):` after `cues = []`, ~line 533): in the
   `if sh["engine"] == "card":` branch, an end card adds
   `{"kind": "endcard", "start": sh["start"] + 0.1, "end": sh["start"] + sh["dur"], "text": brand_text,
   "line": o.get("end_card_line", ""), "logo": bool(logo)}` instead of the card cue.
3. After that loop: `end_main` = start of the end card shot, else total.
   * watermark: if brand_text and o.get("brand_watermark", True): cue kind "brand", 0 .. end_main,
     with "margin_l" = 60 + logo_w + 16 when a logo is shown at a left position.
   * banner: if brand_on and o.get("banner_text"): kind "banner"; start -> 0.3 .. min(5.3, end_main);
     end -> max(0, end_main - 5) .. end_main; always -> 0 .. end_main.
   * `write_ass(..., brand={"color": o.get("brand_color", "#FFB020"), "opacity": float(o.get("brand_opacity", 0.8)),
     "position": o.get("brand_position", "top-left")})`.
4. Logo: `logo = os.path.join(config.BRAND_DIR, o["brand_logo"])` if brand_on and o.get("brand_logo") and the
   file exists, else "". Overlays for `media.assemble(..., overlays=...)`:
   * watermark logo (if watermark on): h 64, opacity = brand_opacity, start 0, end end_main; position
     top-left x 60 y 160; top-center x "(W-w)/2" y 90; top-right x "W-w-150" y 160;
     bottom-left x 60 y "H-470-h". logo_w = round(64 * w / h) from media.image_size(logo).
   * end card logo: x "(W-w)/2", y 640, h 240, opacity 1, start end card start + 0.2, end total.
5. Cover: `media.cover(..., extra_cues=[{"kind": "brand", "start": 0, "end": 5, "text": brand_text}] if
   brand_text else [], brand=<same dict>)`; the cover frame must not be the end card (it already picks the
   first non-card shot).
6. Caption: if brand_on and o.get("caption_add"): caption text + "\n" + caption_add, before the hashtags.
7. Test (CPU only, the project is cached; first set its options):
   `python3 - <<'EOF'` that loads projects/chapter-test/project.json and sets "options" to
   {"brand_text": "voomreel.com", "banner_text": "Free video downloader → voomreel.com",
    "end_card_line": "Download any video at", "caption_add": "🔗 voomreel.com"}; then
   `timeout 300 python3 app/pipeline.py render chapter-test 2>&1 | grep -E "DONE|ERROR" | tail -3`,
   then extract frames 1.0 s, 6.0 s and 1 s before the end to logs/tests/brand_1.png, brand_2.png,
   brand_3.png (`ffmpeg -ss T -i projects/chapter-test/final.mp4 -frames:v 1 -y ...`) and print their sizes.

## Part C: server.py + web GUI

1. `app/server.py` info(): add `"brand_logos": sorted(f for f in os.listdir(config.BRAND_DIR)
   if f.lower().endswith((".png", ".webp")))`. Endpoint next to `/api/sfx/upload`:
   `POST /api/brand/upload?name=` -> `save_upload(self, config.BRAND_DIR, q.get("name", ""), {".png", ".webp"})`
   -> `{"file": <basename>}`.
2. `web/index.html` Settings tab: a new card right after the "Sound and text" card:
```html
      <div class="card">
        <h3>Branding (for marketing)</h3>
        <p class="muted small">Empty = no branding. Used on every new video; switch it off per video on the Render tab.</p>
        <div class="grid2">
          <label>Website / brand (watermark + end card)<input data-s="brand_text" placeholder="e.g. voomreel.com"></label>
          <label>Watermark position<select data-s="brand_position"><option value="top-left">top left</option><option value="top-center">top centre</option><option value="top-right">top right</option><option value="bottom-left">bottom left</option></select></label>
          <label>Watermark opacity<input type="number" step="0.05" min="0.2" max="1" data-s="brand_opacity"></label>
          <label>Logo (PNG, optional)<select data-s="brand_logo"></select></label>
          <label>Banner text<input data-s="banner_text" placeholder="e.g. Free video downloader → voomreel.com"></label>
          <label>Banner shows<select data-s="banner_when"><option value="start">first 5 s</option><option value="end">last 5 s</option><option value="always">the whole video</option></select></label>
          <label>Banner colour<input type="color" data-s="brand_color"></label>
          <label>End card line (above the brand)<input data-s="end_card_line" placeholder="e.g. Download any video at"></label>
          <label>Add to every caption<input data-s="caption_add" placeholder="e.g. 🔗 voomreel.com"></label>
        </div>
        <label class="check"><input type="checkbox" data-s="brand_watermark"> Watermark the whole video</label>
        <label class="check"><input type="checkbox" data-s="end_card"> End card (3 s) with the brand</label>
        <div class="row"><label class="filebtn">Upload logo<input type="file" id="logoFile" accept="image/png,image/webp"></label><span class="muted small" id="logoHint"></span></div>
      </div>
```
   Render tab, after the "Chapter titles" select:
   `<label>Branding<select data-o="branding"><option value="">default (on)</option><option value="off">off for this video</option></select></label>`
3. `web/app.js` fillSettings(): `opts('[data-s="brand_logo"]', I.brand_logos || [], s.brand_logo, 'none');` and add
   'brand_position', 'banner_when' to the list of selects that are set by value. A `$('#logoFile').onchange`
   like `#musicFile` that uploads to `/api/brand/upload?name=...`, then `loadInfo().then(fillSettings)`, selects
   the new logo and says "Logo added. Click Save settings." in #logoHint.
4. Test: `node --check web/app.js && python3 -m py_compile app/server.py`, then start
   `python3 app/server.py --port 8772 &`, `sleep 2`, `curl -s localhost:8772/api/info | python3 -c "import sys,json;print(json.load(sys.stdin)['brand_logos'])"`, kill it.

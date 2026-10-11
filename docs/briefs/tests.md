# Brief: offline test suite

Goal: `python3 tests/run_all.py` runs every test in under 120 s with NO GPU, ComfyUI, Ollama or network,
prints one compact line per test file plus the failures (traceback tail), and exits 1 on any failure.
Stdlib only (`unittest`). Why: a crash in split_line reached Ole's GUI (2026-10-11, project "alberto");
every change must be tested before it is committed.

Rules for the agent: create files under `tests/` only. Do NOT edit `app/`, `web/` or anything else. If
a test finds a real bug, do NOT fix the app and do NOT weaken the test: leave it failing and name it in
your final output. Do not commit. No narration; final output max 10 lines. The scripts in `tests/`
must never touch Ole's data: isolation first.

## Isolation (tests/_isolation.py, imported first by every test file)
Make a temp dir; set `config.SETTINGS_FILE` to a temp settings.json containing
`{"comfy_url": "http://127.0.0.1:9", "ollama_url": "http://127.0.0.1:9"}` (dead ports, so nothing
reaches the real ComfyUI/Ollama); point `config.PROJECTS_DIR`, `EXPORTS_DIR`, `LOGS_DIR`, `MUSIC_DIR`,
`SFX_DIR`, `VOICES_DIR`, `BRAND_DIR` and `media.MUSIC_DIR`, `media.SFX_DIR` (imported by name),
`errors.ERR_LOG`, `errors.FAIL_DIR`, `errors.DEBUG_LOG` into the temp dir (create the folders);
`comfy.configure("http://127.0.0.1:9")`, `llm.configure("http://127.0.0.1:9")`. Remove the temp dir at exit.
Add `app/` to sys.path from the tests folder (no install).

## Part 1 (session 1): runner + pure-Python tests
- `tests/run_all.py`: discover `tests/test_*.py`, run with unittest, print `PASS/FAIL file  n tests  s`
  per file, then failures (last 25 lines of each traceback), then `ALL PASS` or `N FAILED`; exit code.
- `tests/fixtures/`: short SYNTHETIC scripts written by you (do not copy Ole's projects): factory format
  with characters and 2 chapters; markdown/bullet variant with `Scene 1 (0:00-0:04)` and `**Visual:**`;
  a screenplay with `[TITLE CARD 1 — 0:00]` + `TEXT ON SCREEN:` + `(Music shifts: warm piano.)`,
  `[0:10 – 0:50] NAME` blocks with `VISUAL:` / `NARRATION (V.O.) — Low, slow:` (text on the next line in
  quotes) / `AUDIO:` / `ACTION:`, `[FINAL CARD — 1:30]`, and `DIRECTOR'S NOTES:` with `- Color: amber`;
  a plain story text with no scenes; a JSON storyboard inside a ```json fence.
- `tests/test_scriptfmt.py` (app/scriptfmt.py): each fixture parses to the expected scene/chapter counts;
  format -> parse -> format is stable for every fixture; screenplay: title cards are text-only scenes
  (`scriptfmt.is_card`), chapter titles from the cards, block seconds from the timecode (40 for
  0:10-0:50), notes are NOT in any voice line, the colour line is in STYLE, `(Music shifts: x)` is the
  chapter music, AUDIO in the header becomes `ambience`; `female_02: hi` -> speaker female_02;
  `Remember: the light` keeps "Remember:" in the voice and no speaker; `Mara: Cold.` -> speaker Mara when a
  VISUAL mentions Mara; `2024. The storm came.` keeps the year; TEXT `'Twas the night` keeps the
  apostrophe; `DURATION: 1:30` -> 90 s; `ACT ONE: The Arrival` and `PART TWO - Storm` open chapters; a
  repeated `TITLE:` after scenes does not change the title; `template(seconds=600)` mentions CHAPTER and
  `template(seconds=30)` does not; `check()` gives no "no VISUAL" warning for a card.
- `tests/test_planning.py` (app/pipeline.py, no rendering): `split_line` fuzz: 5000 random lines from words
  like "a", "bb", "ccc.", "dd,", "ee" (1-60 words), max_words 8-20: never raises, the pieces joined give
  the same words in the same order, no piece longer than max_words + 2; `plan_shots`: ids unique and
  match `^s\d\d([a-z]|_\d{3})?$`, a card is one shot with `card` true, a silent 30 s scene is split, a
  scene in `keep_whole` is one shot, extra shots carry a `frame`; `estimate_minutes` > 0;
  `_prompt_fixes`: a hearth/fireplace prompt gets "Interior" + a negative, "campfire on a beach" gets
  nothing, "a lighthouse in a storm" gets nothing.
- `tests/test_llm_parsing.py` (app/llm.py, no network): `_to_script` on a valid storyboard JSON using the
  `line` field -> voice text; on "I am sorry" -> `errors.FactoryError` code LLM_OUTPUT with a detail_file
  that exists; `short_hook` cuts to 7 words; `_tidy` keeps the hook text on scene 1 and at most one other
  TEXT; `scriptfmt.from_json` with nested chapters.
- `tests/test_errors.py` (app/errors.py): classify table: "CUDA out of memory" -> GPU_OOM,
  "No space left on device" -> DISK_FULL, ConnectionRefusedError("[Errno 111] Connection refused") with
  component "task:download" -> DOWNLOAD, "task:llm" -> OLLAMA_DOWN, "render" -> COMFY_DOWN, a
  FactoryError keeps its code; `record` writes one JSON line with ref/code/hint/ctx/traceback;
  `recent(ref=...)` finds it; `one_line` contains the code and ref; `save_detail` writes a file.

## Part 2 (session 2): ffmpeg + web API tests
- `tests/test_media.py` (needs ffmpeg, keep it under 60 s): `ass_color` / `text_on` table incl. "#fff",
  "#ffb020", "nonsense"; `caption_cues` start times increase and stay inside the voice duration;
  `write_ass` with brand= contains the Brand, Banner and Endcard styles; `builtin_sfx` for wind, impact,
  fire: duration > 0 and peak below 0 dBFS (ffmpeg volumedetect); `builtin_music("calm", 3, ...)` 3 s;
  `card_clip` and `still_to_clip` (on a generated PNG) 1 s each, duration within 0.05 s; `assemble`
  with 2 clips (crossfade and cut) gives exactly the sum of durs, also with one logo overlay; `mix()`
  with two generated sine wavs gives the right duration.
- `tests/test_server.py`: start `server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)` in a thread
  (isolation first). GET / -> 200 html; GET /api/info -> has "engines", "brand_logos"; POST
  /api/script/parse and /api/script/format round trip; POST /api/projects creates -> GET it -> POST meta
  rename -> POST delete_video -> POST trash (the folder lands in projects/.trash); a POST with header
  `Origin: http://evil.example` -> 403; GET `/files/projects/../settings.json` -> 404; GET /api/errors ->
  {"errors": [...]}; an unknown project -> 400 with "error".

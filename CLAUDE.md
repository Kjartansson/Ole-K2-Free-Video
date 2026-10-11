# Ole-K2-Free-Video: local TikTok video factory

Brief written 2026-10-11 from Ole's requests (Claude chat, 18:30-20:45). Read this first.

## What Ole asked for (his requirements)

1. **Runs with no Claude and no cloud AI.** It makes finished videos WITH sound on this PC.
   Free web AIs (DeepSeek, ChatGPT) are optional, only as a place Ole writes stories.
2. **One command starts it and opens a local web GUI** in the browser.
3. **Script input, two ways:**
   - paste a film script written elsewhere (story, scenes, voice lines, sound), or
   - let the **local LLM write the story/script** from an idea: storytelling, scenes,
     voice lines, sound/music cues. This is a main feature, not a fallback.
   The local LLM also cleans up a pasted script into the factory's format.
4. **Options in the GUI** to change the LLM, the video model (LTX etc.), the voice, quality,
   music, subtitles, and so on.
5. **Model manager in the GUI:** download local models, e.g. from Hugging Face (ComfyUI
   models) and Ollama (LLMs, incl. `hf.co/<user>/<repo>` GGUF), with progress.
6. **Hardware settings:** enter the graphics card's memory (GB) and the PC's RAM (GB), so the
   factory offers/recommends models of the right size and picks fitting quality settings.
   Auto-detect as default, editable.
7. **Output for later upload to TikTok:** final MP4 + caption/description + cover, and a
   library/overview page to copy captions and find files. Nothing is posted automatically.
8. **Usable when tokens are out:** besides the GUI, a plain command that takes a text file
   (`./factory.sh make story.txt`) and builds the video.

## Rules

- Local only: no paid or cloud generation. Downloading models is Ole's click in the GUI;
  an agent never downloads large models on its own without asking Ole.
- Voices: generic voices or Ole's own recording only. The TTS node pack also ships clones of
  real people (Morgan Freeman, David Attenborough, Clint Eastwood, Sophie Anderson, and the
  named higgs voices belinda/chadwick/mabel/vex/broom_salesman): never offer those.
- All text in the video is real rendered text (ASS subtitles via ffmpeg); video models draw
  text as gibberish, so prompts ask for no text.
- Test runs end within 5 minutes (`timeout 300`), long logs go to files, read only summaries.
- Never create accounts. A Hugging Face token (for gated models) is typed by Ole into the GUI
  and stored only locally; never asked for in chat.
- Never upload or schedule on TikTok from the factory.

## Verified on this PC (2026-10-11)

- GPU NVIDIA RTX 4060 Ti, 8 GB VRAM (7.8 usable); RAM ~14 GB; disk 140 GB free; Python 3.14,
  ffmpeg with libass; stdlib only so far (no flask/requests in system python).
- ComfyUI 0.31 at `~/comfy/ComfyUI`, start with `~/comfy/ComfyUI/start.sh` (127.0.0.1:8188).
  Custom nodes: ComfyUI-Manager, ComfyUI-Ollama, TTS-Audio-Suite.
- Models present: `checkpoints/ltxv-2b-0.9.8-distilled.safetensors`,
  `checkpoints/sd_xl_base_1.0.safetensors`, `diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors`,
  `text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors` (LTX),
  `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` + `vae/wan_2.1_vae.safetensors` (Wan),
  `TTS/chatterbox/English` (Chatterbox).
- Ollama (localhost:11434): gemma4:12b, gemma4 (latest), qwen2.5:7b, deepseek-r1:7b,
  llama3.2, plus 16k-context variants. Ollama and ComfyUI cannot both hold the 8 GB GPU:
  call ComfyUI `POST /free` before the LLM and use Ollama `keep_alive: 0` after.
- Timings (incl. model load): SDXL 768x1344 image 19 s; Chatterbox voice line 28 s;
  LTX image-to-video 512x896, 97 frames (4 s at 24 fps) 22 s, good motion;
  LTX text-to-video 512x896, 49 frames (2 s) 16 s; Wan 2.1 t2v 480x832, 33 frames (2 s at 16 fps)
  120 s (25 steps: works but slow on 8 GB, LTX stays the default). Own-voice path verified:
  LoadAudio + CharacterVoicesNode output differs from the generic voice.
  Not yet timed: ACE-Step music, Stable Audio sfx.
- Node names/inputs (from `/object_info`): `ChatterBoxEngineNode` -> `UnifiedTTSTextNode`
  (narrator_voice = path like `voices_examples/female/female_01.wav`) -> `SaveAudio` (flac);
  own voice: `LoadAudio` -> `CharacterVoicesNode(opt_audio_input)` -> `opt_narrator`.
  `LTXVImgToVideo`, `EmptyLTXVLatentVideo`, `LTXVConditioning`, `ModelSamplingLTXV`.
  Wan: `UNETLoader`, `CLIPLoader(type=wan)`, `EmptyHunyuanLatentVideo`, `ModelSamplingSD3`.
  Music: `TextEncodeAceStepAudio`, `EmptyAceStepLatentAudio` (+ 1.5 variants), `VAEDecodeAudio`.
  Sound effects: `EmptyLatentAudio` + `ConditioningStableAudio` (Stable Audio Open);
  TTS-Audio-Suite also has `UnifiedSoundEffectsNode` with MOSS-SoundEffect (model not present).

## Model catalog for the manager (checked on Hugging Face 2026-10-11, all ungated)

| use | repo / file | size | note |
|---|---|---|---|
| image | SG161222/RealVisXL_V5.0 `RealVisXL_V5.0_fp16.safetensors` -> checkpoints | 6.9 GB | photoreal SDXL, openrail++ |
| video | Lightricks/LTX-Video `ltxv-2b-0.9.8-distilled-fp8.safetensors` -> checkpoints | 4.5 GB | for <8 GB cards |
| video | Lightricks/LTX-Video `ltxv-13b-0.9.8-distilled-fp8.safetensors` | 15.7 GB | 16 GB+ VRAM, 32 GB RAM |
| video | Lightricks/LTX-Video `ltxv-spatial-upscaler-0.9.8.safetensors` | 0.5 GB | sharper LTX |
| video | Comfy-Org/Wan_2.2_ComfyUI_Repackaged `split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors` + `split_files/vae/wan2.2_vae.safetensors` | 10 + 1.4 GB | 12 GB+ VRAM / 24 GB+ RAM suggested |
| music | Comfy-Org/ACE-Step_ComfyUI_repackaged `all_in_one/ace_step_v1_3.5b.safetensors` -> checkpoints | 7.7 GB | apache-2.0 |
| music | Comfy-Org/ace_step_1.5_ComfyUI_files `checkpoints/ace_step_1.5_turbo_aio.safetensors` | 10 GB | apache-2.0, newer |
| sound fx | Comfy-Org/stable-audio-open-1.0_repackaged `stable-audio-open-1.0.safetensors` -> checkpoints + google-t5/t5-base `model.safetensors` -> text_encoders (rename t5_base.safetensors) | 4.9 + 0.9 GB | Stability community license |
| upscale | lllyasviel/Annotators `RealESRGAN_x4plus.pth` -> upscale_models | 0.07 GB | image upscaler |

Download URL form: `https://huggingface.co/<repo>/resolve/main/<path>` (stream + resume with Range).
Each catalog entry should carry min VRAM / min RAM so the hardware settings can mark it
fits / tight / too big.

## How it is built (2026-10-11, all stdlib Python + vanilla JS)

Ole's later wishes (same day): ChatGPT/DeepSeek copy-paste flow is the main path; a fill-in
template that shows what a video needs; project name + output file name chosen in the GUI;
portable across workstations (no hard-coded paths, ComfyUI/Ollama by URL); plain install
instructions, no Docker (GPU passthrough + 20 GB images not worth it for a few PCs).

| file | role |
|---|---|
| `factory.py` (+ `factory.sh`, `factory.bat`) | launcher: starts ComfyUI (if local + down) and Ollama, serves the GUI on 127.0.0.1:8770, opens the browser; `make <file>`, `write "idea"`, `doctor`, `shortcut` |
| `app/config.py` | `settings.json` (per workstation, gitignored), hardware detect (ComfyUI `/system_stats`, nvidia-smi, /proc/meminfo, Windows/macOS), quality presets, recommendations, fit |
| `app/scriptfmt.py` | script format: tolerant parser (markdown, bullets, `Scene 1 (0:00-0:04)`, JSON, `//` comments), formatter, checker, ChatGPT prompt template, character-look injection |
| `app/llm.py` | Ollama: two-pass story writing (beats text -> JSON schema storyboard), convert, improve, pull, delete, unload; `think:false` for thinking models; scene field is `line` (not `voice`: the model confused it with voice names) |
| `app/comfy.py` | ComfyUI over HTTP only (upload, queue, `/view` download), so ComfyUI may be on another PC; SDXL, LTX i2v/t2v, Wan 2.1, Chatterbox TTS (+own voice), ACE-Step music, Stable Audio sfx; voice allowlist (no celebrity clones) |
| `app/media.py` | ffmpeg: Ken Burns on stills, fit clips to 1080x1920/30 fps, ASS karaoke subtitles, 14 built-in sfx + 4 music moods synthesised (limiter `level=false`), ducking mix, loudnorm -14 LUFS, cover |
| `app/pipeline.py` | render a project stage by stage (voices -> pictures -> animation -> clips -> sound -> final), cache keys in `work/cache.json`, `--scene N` preview, LTX failure falls back to camera move, export with unique name |
| `app/projects.py` | `projects/<slug>/` (script.txt, project.json, work/, final.mp4, cover.jpg, caption.txt, job.json, render.log); rename moves the folder; trash = `projects/.trash` |
| `app/models.py` | catalog (12 HF files, all URLs + sizes verified 10-11), fit, resumable download + SHA-256 check against HF's LFS oid, custom HF link, GGUF search for Ollama `hf.co/...` |
| `app/server.py` | ThreadingHTTPServer JSON API + static + Range; background tasks (LLM, downloads, pulls, voice preview); render = subprocess, one at a time; cross-origin POSTs refused |
| `web/` | tabs Story / Scenes / Render / Library / Models / Settings / Help |
| `templates/`, `examples/` | fill-in template, ChatGPT prompt, lighthouse example |
| `START-HERE.md`, `INSTALL.md`, `requirements.txt` | how to start, how to install on another PC (factory needs no pip packages) |

## Long videos + automatic splitting (Ole 2026-10-11 ~21:20)

Ole: "possible to generate a 10 min video, a lot of small videos cut good together" and "it should
automatically split the script to what it can handle". Built and tested:
- Script: `CHAPTER n: title` (+ optional chapter `MUSIC:`), scenes carry `chapter`; repeated header
  lines from a web AI's later replies are absorbed; plain story text without scenes -> scenes.
- LLM: >120 s = outline with exactly N chapters (schema minItems/maxItems + one retry) then one call
  per chapter with bible + "story so far" (no repeats); convert >700 words in ~450-word parts;
  improve >24 scenes in 12-scene blocks. Tested 3 min / 3 chapters / 36 scenes in 88 s (gemma4).
- Pipeline plans SHOTS: voice lines over max_words (15 on 8 GB, from the LTX frame cap) split at
  sentence/clause breaks; extra shots get another framing (close-up, wide, ...) + seed; long silent
  scenes split too; a shot still longer than ~1.3 clips chains LTX from its own last frame.
  Files are per shot id (s02, s02b, s02c). Crossfade 0.35 s (fadeblack at chapter starts),
  assembled in chunks of 12 then one final encode with subtitles + audio. Sound beds run across
  shots that share a sound; music per chapter (key shift for built-in), crossfaded. Film look
  (grade + grain). Render-time estimate in the GUI.
- Test: 3 scenes / 2 chapters -> 5 shots, 14.7 s, 3 min render (estimate 4.1), -15 LUFS,
  crossfade + chapter title + beds verified on frames.

## Screenplay scripts + title cards (Ole 2026-10-11 ~21:45)

Ole pasted a ChatGPT screenplay ("WHAT THE FIRE KEEPS": `[0:10 – 0:50] NAME` blocks, `NARRATION (V.O.) — …:`,
`TEXT ON SCREEN`, `[TITLE CARD n]`, `(Music shifts: …)`, `DIRECTOR'S NOTES`). The parser now reads that:
bracket blocks = scenes (length from the timecode; per-scene cap raised 20 -> 300 s), title cards =
text-only scenes that open a chapter (chapter title + music), final card = text-only scene, SETTING/
FORMAT/Color go into STYLE, header AUDIO = new AMBIENCE field (a soft bed under the whole video),
notes are skipped, `(stage directions)` are dropped, voice-name speakers (`female_02: line`) use that
voice. Text-only scenes render as title cards (`media.card_clip` + serif centred "Card" subtitle).
GUI: newest project opens by itself, empty Scenes/Render tabs list projects, "Save and render video"
button. Server reloads app/*.py by itself when they change (server.py itself still needs a restart).

Kimi read-only review of the parser (10-11 ~22:00, `logs/tests/kimi_review.txt`): fixed `DURATION: 1:30`
(was 30 s), shot ids past 26 parts (`s01_027`), "Remember: ..." eaten as a speaker (now an unknown
Title-case name only counts if the pictures mention it; ALL-CAPS names still count, so "NASA: ..." is a
known false positive), "2024. ..." losing the year, apostrophes stripped from 'Twas, two fenced JSON
blocks, "ACT ONE"/"PART TWO" chapters, last-piece merge capped at max_words + 2.

## State (2026-10-11 22:15, Kimi session)

- Everything above works on this PC. Projects on disk: `smoke-test`, `chapter-test` (rendered),
  `test-lighthouse` (6-scene example, saved, not rendered: a good first test for Ole).
- Verified this session (Kimi): `factory.sh doctor` all OK; LTX text-to-video (16 s for 2 s clip);
  Wan 2.1 text-to-video (120 s for 2 s clip, works but slow); own-voice upload flow
  (reference WAV uploaded, cloned output verified different from generic voice).
  First git commit made (repo had no commits before).
- NOT tested yet: ACE-Step music, Stable Audio sfx (models not downloaded — Ole clicks in the GUI),
  GUI downloads/pulls, Windows launcher, a full 10-minute render (expect ~1.5-2.5 h on 8 GB).

## Next steps (when Ole asks)

1. Ole tests the GUI himself (START-HERE.md) and reports what to change.
2. A real 10-minute render as a soak test (overnight).

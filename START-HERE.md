# START HERE: Ole-K2 Free Video

Makes finished TikTok videos with sound on your own PC. No Claude, no cloud AI, no tokens.

## 1. Start the program

**Linux** (this PC): open a terminal and run

```bash
cd ~/Ole-K2-Free-Video
./factory.sh
```

**Windows**: double-click `factory.bat`.

The launcher:
1. starts ComfyUI if it is not running yet (this takes 20-60 s the first time),
2. starts Ollama if needed,
3. opens the web GUI at **http://127.0.0.1:8770** in your browser.

ComfyUI stopped while the factory is open? Click the red **ComfyUI** dot at the top of the page, or
just click **Render video**: the factory starts ComfyUI by itself (20-60 s) and then renders.

Already running? `./factory.sh` then just opens the browser (and starts ComfyUI if it is down). To load new code: `./factory.sh restart`
(renders that are running keep going). `./factory.sh stop` stops it. No `sudo` needed.

Leave the terminal window open while you work. **Ctrl+C** (or closing the window) stops the
program, and also stops the ComfyUI it started.

Want an icon in the app menu (Linux)? Run once: `./factory.sh shortcut`

## 2. Make a video in the GUI

| Step | Tab | What you do |
|---|---|---|
| 1 | **Story** | Get a script: **Write with local AI** (type an idea, click *Write story*), or **From ChatGPT / DeepSeek** (click *Copy prompt*, paste it into the web AI, paste its answer into the Script box), or **Template** (fill it in yourself). Click **Check**. |
| 2 | **Story** | Type a **Project name** and an **Output file name**, click **Save as new project**. |
| 3 | **Scenes** | Each scene is a card. Fix the text, click **Preview** to make only that scene, or **New picture** to try another image. |
| 4 | **Render** | Choose options (or keep the defaults) and click **Render video**. A 30 s video takes roughly 3-8 minutes on an 8 GB card. |
| 5 | **Render / Library** | Watch it, **Copy caption**, **Open exports folder**, and upload the MP4 to TikTok yourself. Tick **posted** in the Library. |

The finished file is in `exports/` with the name you chose (or `date_project-name.mp4`),
next to a `.txt` with the caption and a `.jpg` cover.

## Long videos (2-15 minutes)

- Pick a length up to **15 min** on the Story tab. The local AI plans the chapters first, then writes
  them one by one (about 30 s per chapter on this PC), so it keeps the story straight.
- From ChatGPT/DeepSeek: the copied prompt asks for **one chapter per answer**. Type `next` for the
  next one and paste all the answers, one after another, into the Script box.
- Plain story text with no scenes works too: every sentence or two becomes a scene.
  **Convert with local AI** gives better pictures and handles long texts in parts.
- The factory adapts the script to this PC automatically: voice lines longer than one video clip
  (about 15 words on an 8 GB card) become extra shots of the same scene (close-up, wide, other angle),
  every cut crossfades, sounds like rain run on across cuts, the music changes per chapter, and chapter
  titles appear on screen.
- The Render tab shows a time estimate. A 10-minute video takes roughly 1.5-2.5 hours on an 8 GB
  card. If you cancel or the PC restarts, click **Render video** again: finished parts are reused.

## 3. Without the GUI (plain commands)

```bash
./factory.sh make examples/lighthouse.txt
```

```bash
./factory.sh make my-story.txt --name "Lighthouse" --output lighthouse-part1 --engine still
```

```bash
./factory.sh write "a cat that runs a night bakery" cat.txt --seconds 30 --genre comedy
```

```bash
./factory.sh write "a lighthouse keeper and a ghost ship" ghost.txt --seconds 600 --genre mystery
```

```bash
./factory.sh doctor
```

`make` turns a script file into a video. `write` lets the local LLM write the script file.
`doctor` checks this PC and tells you what is missing.

## 4. The script format

See `templates/video-template.txt` (every field explained) and `examples/lighthouse.txt`
(a finished example). The prompt for ChatGPT/DeepSeek is `templates/copy-to-chatgpt.txt`.
Only one thing is required: at least one `SCENE` with a `VISUAL`.

## 5. Settings worth knowing

- **Settings > This PC**: graphics card memory and RAM (auto-detected, you can type them).
  Quality, engine and LLM suggestions follow from these.
- **Settings > Voice**: pick the narrator, test it with *Hear voice*, or record your own voice.
- **Models tab**: download more models (story LLMs via Ollama, picture/video/music models from
  Hugging Face). Each one shows whether it fits this PC.
- **Another workstation**: copy this folder there and follow `INSTALL.md`. Each PC keeps its own
  `settings.json`. ComfyUI or Ollama can also run on a different PC: put its address in
  *Settings > Connections*.

## If something goes wrong

- The GUI shows what went wrong, a hint what to do, and a **Copy error report** button. Paste that
  report to Claude or Kimi: it has everything needed to find the cause.
- **Help tab > Recent problems** lists the latest errors.
- In a terminal:

```bash
./factory.sh errors
```

```bash
./factory.sh report
```

`errors` lists the latest problems. `report` writes one file with everything (give that file to
Claude or Kimi). `./factory.sh doctor` checks the installation.
- Out of graphics memory (code GPU_OOM)? The factory retries once by itself. If it still fails, use
  quality **draft** or the engine **Image + camera move**, and render again: finished parts are kept.

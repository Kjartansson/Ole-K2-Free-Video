# Ole-K2 Free Video

A local TikTok / Reels video factory. Script in, finished vertical video with voice, music,
sound effects, subtitles, cover and caption out. Runs on your own PC: no Claude, no cloud AI,
no tokens. Nothing is posted automatically.

- **Start it:** `./factory.sh` (Linux) or `factory.bat` (Windows), then use the browser page.
  Full guide: [START-HERE.md](START-HERE.md)
- **Install on another PC:** [INSTALL.md](INSTALL.md)
- **Script format:** [templates/video-template.txt](templates/video-template.txt), example
  [examples/lighthouse.txt](examples/lighthouse.txt), prompt for ChatGPT/DeepSeek
  [templates/copy-to-chatgpt.txt](templates/copy-to-chatgpt.txt)

How a video is made: voice per scene (Chatterbox) sets the scene length, SDXL paints the
picture, LTX-Video animates it, ffmpeg fits every clip to 1080x1920, adds karaoke subtitles
and on-screen text, mixes voice + sound effects + music (ducked under the voice, -14 LUFS),
and writes `final.mp4`, `cover.jpg` and `caption.txt`, plus a copy in `exports/`.

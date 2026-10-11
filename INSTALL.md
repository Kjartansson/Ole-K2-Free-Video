# Install on a new workstation

The factory itself is plain Python (standard library only, no `pip install`). It needs four
programs next to it. Steps for Linux and Windows below. Afterwards run `doctor` to check.

| Needed | Why | Size |
|---|---|---|
| Python 3.9+ | runs the factory | small |
| ffmpeg (with libass) | cuts, subtitles, sound mix | ~100 MB |
| Ollama | the local LLM that writes stories | ~1 GB + models (2-9 GB each) |
| ComfyUI + TTS Audio Suite | pictures, animation, voices | ~10 GB + models (~25 GB for the basic set) |
| NVIDIA or Intel Arc graphics, 8 GB+ | speed (6 GB works with draft quality) | |

Disk: plan for about **60 GB** free.

## Intel Arc instead of NVIDIA

ComfyUI runs on Intel Arc cards (discrete A/B-series and the Arc graphics built into newer
Core Ultra laptops, e.g. HP EliteBook X) through PyTorch's XPU backend. Differences from the
normal install:

- Install the **newest Intel graphics driver** first.
- ComfyUI is installed **manually** (the ready-made portable package is NVIDIA-only). Follow
  the Linux steps above on either system, but replace the torch line with the XPU build:

  ```bash
  venv/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/xpu
  ```

  On Windows use `venv\Scripts\pip` instead. Start ComfyUI once by hand to check it sees the
  Arc card; after that the factory starts it by itself.
- **Arc laptops share RAM as VRAM.** "16 GB VRAM" is shared with the system, so 32 GB RAM is
  strongly recommended; the story LLM (Ollama) then runs on the CPU while ComfyUI holds the
  shared memory. Type the VRAM number in *Settings > Hardware* if detection shows 0.
- Expect renders to be slower than on an NVIDIA card with the same memory, and start with the
  suggested quality preset. If a voice or music node fails on Arc it falls back to the CPU:
  that works, just slower. NPU chips (AI Boost etc.) are not used: no video model runs on them.

## Linux (Ubuntu / Debian)

```bash
sudo apt install python3 python3-venv git ffmpeg fonts-noto-core
```

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

```bash
ollama pull qwen2.5:7b
```

ComfyUI (in your home folder):

```bash
git clone https://github.com/comfyanonymous/ComfyUI ~/comfy/ComfyUI
```

```bash
cd ~/comfy/ComfyUI && python3 -m venv venv && venv/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 && venv/bin/pip install -r requirements.txt
```

TTS Audio Suite (the voice node) and ComfyUI-Manager:

```bash
cd ~/comfy/ComfyUI/custom_nodes && git clone https://github.com/ltdrdata/ComfyUI-Manager && git clone https://github.com/diodiogod/TTS-Audio-Suite
```

```bash
cd ~/comfy/ComfyUI && venv/bin/pip install -r custom_nodes/TTS-Audio-Suite/requirements.txt
```

Get the factory:

```bash
git clone https://github.com/Kjartansson/Ole-K2-Free-Video.git ~/Ole-K2-Free-Video
```

```bash
cd ~/Ole-K2-Free-Video && ./factory.sh doctor
```

Later updates: `git -C ~/Ole-K2-Free-Video pull`

## Windows 10/11

1. **Python**: install from https://www.python.org/downloads/ (tick *Add python.exe to PATH*).
2. **ffmpeg**: in PowerShell `winget install Gyan.FFmpeg`, then open a new window.
3. **Ollama**: install from https://ollama.com/download, then in PowerShell `ollama pull qwen2.5:7b`.
4. **ComfyUI**: download *ComfyUI portable for Nvidia* from
   https://github.com/comfyanonymous/ComfyUI/releases and unpack it to
   `C:\ComfyUI_windows_portable` (the factory finds it there; any other place: set the folder in
   Settings).
5. **TTS Audio Suite**: start ComfyUI once (`run_nvidia_gpu.bat`), open http://127.0.0.1:8188,
   *Manager > Custom Nodes Manager*, search **TTS Audio Suite**, Install, restart ComfyUI.
6. Get the factory: install Git for Windows (`winget install Git.Git`), then in PowerShell
   `git clone https://github.com/Kjartansson/Ole-K2-Free-Video.git` (or download the ZIP from
   https://github.com/Kjartansson/Ole-K2-Free-Video). Double-click `factory.bat`.
   Check with `factory.bat doctor` in a terminal.

## Models (downloaded from the GUI)

Open the **Models** tab. The basic set:

| Model | For | Size |
|---|---|---|
| SDXL base 1.0 (or RealVisXL) | the picture for every scene | 6.9 GB |
| LTX-Video 2B distilled (fp8 on 6 GB cards) | animates the pictures | 6.3 GB (4.5) |
| T5-XXL text encoder | needed by LTX | 5.2 GB |
| a story LLM in Ollama, e.g. `qwen2.5:7b` | writes scripts | 4.7 GB |

The voice model (Chatterbox, ~2 GB) downloads by itself the first time a voice is made.
Optional: ACE-Step (AI music), Stable Audio Open (AI sound effects), Wan 2.1 (another video
engine). Without them the factory uses built-in music and sound effects.

## Several workstations

- Each PC has its own `settings.json` (hardware, addresses, defaults). Copying the folder does
  not copy `settings.json`, projects or exports unless you copy those too.
- One strong GPU PC can serve the others: start ComfyUI there with
  `--listen 0.0.0.0` and Ollama with `OLLAMA_HOST=0.0.0.0`, then enter
  `http://<that-pc>:8188` and `http://<that-pc>:11434` in *Settings > Connections* on the
  other PCs. Models are then downloaded on the GPU PC (its Models tab).
- `./factory.sh --host 0.0.0.0` makes the GUI itself reachable from other PCs. It has **no
  password**: only do this on your own home network.

## Why not Docker?

It works with a GPU, but only after installing the NVIDIA Container Toolkit (Linux) or WSL2
(Windows), the image is 20+ GB and the models still live outside it. Plain installs are simpler
for a few workstations. If you later want Docker on Linux GPU machines, the factory needs
nothing special: run it next to the official ComfyUI and Ollama containers and point
*Settings > Connections* at them.

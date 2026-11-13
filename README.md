# mGBA Python Replica & ROM Hacking Studio

A full-featured Game Boy Advance emulator and forensic ROM hacking suite built with **Python 3.10+**, **PyQt6**, and the native **mGBA Libretro Core** via `pdretro`. 

Equipped with native hardware sensor emulation (Game Boy Player, Cartridge Rumble, Boktai Solar, Gyro/Tilt), a vectorized audio DSP pipeline, in-game graphic and hex editors, a translation/font studio, deterministic replay recording, and real-time AI screen translation.

---

## 🌟 Key Features

### 🎮 1. Emulation Core & Input
- **mGBA Libretro Backend**: Powered by `pdretro` loading native `mgba_libretro` core binaries (`.dll`, `.so`, `.dylib`).
- **Native Game Boy Player (GBP) & Rumble**:
  - Direct CTypes hook into the Libretro environment interface (`RETRO_ENVIRONMENT_GET_VARIABLE` & `RETRO_ENVIRONMENT_GET_RUMBLE_INTERFACE`).
  - Native haptic vibration for **Game Boy Player** (*Pokémon Pinball: Ruby & Sapphire*, *Mario & Luigi*, *Super Mario Advance 4*), **Cartridge Rumble** (*Drill Dozer*, *Goodboy Galaxy*), and **Gyro Rumble** (*WarioWare: Twisted!*).
  - Dual-driver rumble output: Native 64-bit Windows **XInput** (slots 0–3) with fallback to **Pygame / SDL2**.
- **Hardware Sensors & Peripherals**:
  - **Solar Sensor (Boktai)**: 11 sunlight levels (0–10) with hotkeys and on-screen status.
  - **Gyro / Tilt Simulation**: Controlled via analog thumbsticks, keyboard (`Q`/`E`), or mouse drag-twist gesture (Right/Middle-click).
- **Controls & Autofire**: Full keyboard and gamepad remapping with per-button turbo/autofire toggles and adjustable stick deadzones.
- **Save States & Rewind/Replay**: 9 state slots, arbitrary file save/load, and single-frame stepping.
- **Fast-Forward**: Configurable multipliers (1.5x up to 10.0x) with cycle hotkeys (`=` and `-`).
- **Focus Auto-Pause**: Automatically pauses emulation when switching windows.

---

### 🎨 2. Video & Responsive UI
- **Zero-Pillarbox Scaling**: Pixel-perfect scaling (1x to 6x) maintaining GBA's native $3:2$ ($240 \times 160$) aspect ratio without letterboxing or black side bars.
- **Responsive Multi-Tier Sensor HUD**:
  - **Compact Tier (< 380px wide / 1x scale)**: Condensed badges (`[BPPE]  ☀️ 5/10  📳 OFF  🎯 0°`).
  - **Medium Tier (380px–650px wide / 2x scale)**: Balanced textual readouts.
  - **Full Tier (≥ 650px wide / 3x+ scale)**: Full graphical progress bars and dial indicators.
- **Interframe Blending & Frameskip**: Authentic LCD ghosting / motion blur simulation and configurable frameskip (0–5).
- **Pixel Ruler Overlay**: On-screen drag-and-measure tool for sprite/box dimensions and GBA coordinate inspection.
- **Persistent Geometry**: Window width, height, splitter row distributions, active scale factor, and HUD preferences persist across launches.

---

### 🔊 3. Vectorized Audio DSP Pipeline
Zero-dependency, NumPy-accelerated audio processing:
- **Low-Pass Filters (FIR Kernels)**: Authentic vintage warm speaker simulation (*Light ~12 kHz*, *Medium ~8 kHz*, *Heavy ~5 kHz*).
- **Stereo Widener**: Mid-Side matrix expansion for immersive stereo separation.
- **Bass Boost**: ~300 Hz low-pass filter extraction and bass injection.
- **Soft Limiter**: Analog `tanh` saturation curve to eliminate clipping and distortion.
- **Direct Sound Output**: Low-latency 44.1 kHz 16-bit PCM playback via `sounddevice`.

---

### 🛠️ 4. ROM Hacking & Forensic Suite

#### 🧩 In-Game Graphic Editor (YY-CHR Replica)
- Real-time VRAM pattern table viewer (Charblocks 0–5 or continuous 96 KB VRAM) and OAM sprite inspector.
- **Drawing Tools**: Pencil, Eraser, Line, Eyedropper, Flood Fill, Rectangle (Stroke/Fill), Ellipse (Stroke/Fill) with customizable brush sizes (1–16 px).
- 4bpp (16 colors) and 8bpp (256 colors) graphics editing.
- Live palette color editing with RGB picker and direct PRAM synchronization.
- **ROM Patcher & Exporter**: Direct VRAM writing, raw VRAM dump export (`.bin`), PNG graphics export, and automatic **IPS Patch generation** with built-in **BIOS LZ77 (Type 0x10)** decompressor and compressor.

#### 🔍 Interactive Hex Editor (HxD / ImHex Style)
- Virtualized scrollable memory viewer capable of rendering large regions at 60 FPS.
- Supports all GBA memory regions: **EWRAM**, **IWRAM**, **VRAM**, **PRAM**, **OAM**, **ROM**, and **SRAM**.
- **Live RAM Watch**: Real-time memory polling with green diff highlighting for dynamic values.
- **Data Inspector**: Decodes values at cursor into `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, GBA BGR555 color swatch, and GBA pointer targets with a one-click **Follow Pointer** button.
- Hex & ASCII search/replace, bookmarking, and multi-format copy (Raw Hex, C Array, Text, Address).

#### 🔬 Forensic Dev Tools
- **Instant AI Scene Dump**: One-click export bundling composited `screen.png`, isolated layer PNGs (`BG0`–`BG3`, `OBJ`), sprite metadata, Capstone ARM/Thumb disassembly, comprehensive `scene_data.json`, and an exact `.state` save snapshot.
- **Palette Viewer**: Visual inspection and PNG export of 16 Background and 16 Sprite color banks.
- **Sprite Viewer (OAM)**: Attribute table for 128 hardware sprites with preview and sheet exporter.
- **Tilemap Viewer**: Renders tilemaps across all 32 Screen Base Blocks (SBBs).
- **Memory Searcher**: Cheat finder supporting `uint8`, `uint16`, `uint32` across exact, relative, and changed/unchanged comparisons.
- **I/O Register Viewer**: Real-time inspection of LCD, Sound, DMA, Timers, and Interrupt flags.

---

### 🌐 5. Translation Studio & Font Tools
- **Text Inspector & Script Auto-Repointer**:
  - Global encoding manager supporting **ASCII**, **Shift-JIS**, and **Custom Hex `.tbl`** files.
  - Automatic pointer table scanner and script dumper.
  - CSV export/import workflow for script translation.
  - Automated free-space injector and pointer table patcher.
  - **Relative Value Search**: Discovers unknown text encodings by mathematical character distance.
  - **WYSIWYG Live RAM Injector**: Injects translated text directly into live memory with Screen Ruler coordinate auto-detection.
- **Font Studio & TTF Generator**:
  - ROM Font Ripper for 1bpp, 2bpp, and 4bpp tile fonts.
  - TrueType/OpenType (`.ttf` / `.otf`) to GBA binary generator with automated **Variable Width Font (VWF)** width table calculation.

---

### 🤖 6. AI Screen Translation & Session Recorder
- **AI Screen Translation (Hotkey: `T`)**:
  - Captures on-screen dialogue, menus, and labels via OCR (MeikiOCR / PaddleOCR / EasyOCR) and local LLM inference (Qwen GGUF via `llama-cpp-python`).
  - Formatted subtitle dock with categorized dialogue, speaker identification, and menu translations.
- **Deterministic Session Recorder**:
  - Records gameplay inputs alongside an initial `.state` snapshot into an AI-friendly structured JSON replay format.
  - Generates semantic summary action intervals for LLM analysis.
  - Frame-accurate deterministic playback.

---

## 📂 Project Structure

```text
GBA/
├── gba_studio/
│   ├── core/                # Emulator, configuration, audio, cheats, and replay services
│   ├── ui/                  # Main window, video/HUD widgets, controllers, and shaders
│   ├── tools/               # ROM editors, memory viewers, translation, and forensics
│   │   ├── ai/              # OCR, vision translation, local translation, and worker
│   │   └── graphics/        # Graphic editor, canvas widgets, and IPS/LZ77 codecs
│   └── paths.py             # Shared project-root path
├── main.py                  # Application entry point
├── bundle_codebase.py       # Source-context bundler utility
├── mgba_libretro.dll       # mGBA Libretro Core binary (Windows x64)
├── mgba_config.json        # User configuration file
├── requirements.txt        # Python dependency manifest
├── start.bat               # Windows launcher script
├── start.ps1               # PowerShell launcher script
└── start.sh                # Linux / macOS launcher script
```

---

## 🚀 Getting Started

### Prerequisites
- **Python 3.10+** (64-bit recommended)
- **mGBA Libretro Core**:
  - Windows: `mgba_libretro.dll` (included in root)
  - Linux: `mgba_libretro.so`
  - macOS: `mgba_libretro.dylib`

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/gba-python-studio.git
   cd gba-python-studio
   ```

2. **Create and activate a virtual environment:**
   ```bash
   # Windows (Command Prompt / PowerShell)
   python -m venv .venv
   .venv\Scripts\activate

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the emulator:**
   - **Windows:** Double-click `start.bat` or run:
     ```powershell
     .\start.ps1
     ```
   - **Linux / macOS:**
     ```bash
     chmod +x start.sh
     ./start.sh
     ```
   - **Direct Python launch:**
     ```bash
     python main.py
     ```

---

## ⌨️ Default Controls & Hotkeys

### Keyboard Bindings

| GBA Button | Default Key | GBA Button | Default Key |
| :--- | :--- | :--- | :--- |
| **D-Pad Up** | `Up Arrow` | **A Button** | `Z` |
| **D-Pad Down** | `Down Arrow` | **B Button** | `X` |
| **D-Pad Left** | `Left Arrow` | **L Shoulder** | `A` |
| **D-Pad Right** | `Right Arrow` | **R Shoulder** | `S` |
| **Start** | `Enter / Return` | **Select** | `Shift / Tab` |
| **Autofire A** | `C` | **Autofire B** | `V` |
| **Autofire L** | `D` | **Autofire R** | `F` |

### Hotkeys & Shortcuts

| Action | Shortcut | Description |
| :--- | :--- | :--- |
| **Load ROM** | `Ctrl + O` | Open file dialog for GBA/GB/GBC ROMs |
| **Pause / Resume** | `Ctrl + P` | Toggle emulation pause state |
| **Step 1 Frame** | `Ctrl + N` | Advance emulation by exactly one frame |
| **Reset Game** | `Ctrl + R` | Soft-reset the emulator core |
| **Fast-Forward Toggle** | `Tab` | Hold or toggle fast-forward speed |
| **Speed Up Fast-Forward** | `=` | Increase fast-forward ratio (+0.5x) |
| **Slow Down Fast-Forward** | `-` | Decrease fast-forward ratio (-0.5x) |
| **Quick Save State** | `Shift + F10` | Save state to active slot |
| **Quick Load State** | `F10` | Load state from active slot |
| **State Slots 1–9** | `F1` – `F9` | Select active save state slot |
| **Take Screenshot** | `F12` | Save lossless PNG to `screenshots/` |
| **Translate Screen (AI)** | `T` | Trigger OCR & LLM subtitle translation |
| **Instant Scene Dump** | `Ctrl + Shift + D` / `F11` | Generate full forensic dossier & `.state` snapshot |
| **Graphic Editor (YY-CHR)**| `Ctrl + G` | Open in-game sprite and tile editor |
| **Hex Editor** | `Ctrl + H` | Open memory and save hex studio |
| **Brighten Sunlight** | `]` | Increase Boktai solar level (+1) |
| **Darken Sunlight** | `[` | Decrease Boktai solar level (-1) |
| **Gyro / Tilt Left** | `Q` | Twist virtual tilt sensor -15° |
| **Gyro / Tilt Right** | `E` | Twist virtual tilt sensor +15° |
| **Center Gyro / Tilt** | `Backspace` | Reset tilt/gyro sensors to 0° neutral |

---

## 🎯 Special Hardware & Game Features

### Pokémon Pinball: Ruby & Sapphire (Game Boy Player Rumble)
- The emulator natively identifies ROM header code `BPPE` / `BPPJ` / `BPPP`.
- Automatic Libretro Environment bridging activates mGBA's internal Game Boy Player serial interface.
- In-game **OPTIONS → RUMBLE** unlocks normally (**ON / OFF**). Pressing `A` on the rumble setting triggers the game's internal rumble test without deadlock or freezing.
- Physical controller vibration triggers on real flipper strikes, bumper hits, slingshot deflections, and Pikachu saves.

### Drill Dozer & Goodboy Galaxy (Cartridge Rumble)
- Identifies game codes `V49E` / `V49J` / `V49P` and `2GBP`.
- Emulates cartridge motor commands, driving Xbox / SDL2 controller motors during drilling and heavy impacts.

### WarioWare: Twisted! (Gyro & Rumble)
- Identifies `RZWE` / `RZWJ` / `RZWP`.
- Rotational wheel mechanics are controlled via right-click mouse drag, analog stick, or `Q`/`E` keys, with tactile motor feedback on clicks and goal completions.

### Boktai 1 & 2 (Solar Sensor)
- Real-time photometric sensor level adjustments (levels 0–10) using `[` and `]`.

---

## ⚙️ Configuration (`mgba_config.json`)

Settings are saved automatically on change or application exit in `mgba_config.json`:

```json
{
    "audio": {
        "mute": false,
        "volume": 0.8,
        "low_pass_filter": "medium",
        "stereo_widener": true,
        "bass_boost": false,
        "soft_limiter": true
    },
    "emulation": {
        "skip_bios": true,
        "fast_forward_ratio": 2.0,
        "state_slot": 1,
        "pause_on_unfocus": true
    },
    "sensors": {
        "solar_level": 5,
        "rumble_enabled": true,
        "rumble_strength": 1.0,
        "gyro_enabled": true,
        "mouse_gyro": true,
        "show_hud": true
    },
    "video": {
        "frameskip": 0,
        "interframe_blending": false,
        "scale": 3,
        "window_width": 720,
        "window_height": 649,
        "splitter_sizes": [480, 30, 125]
    }
}
```

---

## 📦 Requirements & Dependencies

- **PyQt6**: Modern Qt6 GUI components, dialogs, and event loop.
- **pdretro**: Libretro core loader and frame execution wrapper.
- **sounddevice**: Low-latency multi-channel audio streaming.
- **pygame**: SDL2 controller, joystick, and hat/axis polling.
- **numpy**: Vectorized audio DSP, memory array slicing, and tile indexing.
- **Pillow (PIL)**: Tile graphics, palettes, sprites, and forensic PNG exports.
- **capstone**: Real-time ARM7TDMI ARM & Thumb disassembly.
- **meikiocr / paddleocr / easyocr**: Retro game Japanese & English OCR.
- **llama-cpp-python**: Local GGUF large language model inference for game translation.

---

## 📜 Acknowledgements & Credits

- **[mGBA](https://mgba.io/)** by Vicki Pfau (endrift) — Industry-standard Game Boy Advance emulation.
- **[Libretro / RetroArch](https://www.libretro.com/)** — Cross-platform emulation API.
- **[GBATEK](https://problemkaputt.de/gbatek.htm)** by Martin Korth — Technical specifications on GBA hardware, SIO, and Game Boy Player communication.
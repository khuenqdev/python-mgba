# Roadmap & Future Enhancements Checklist

### 1. Advanced AI Translation & Localization Workstation

- [ ] 1. **Auto-Translate on Scene / Dialogue Change (Smart Auto-Trigger)**

  * **Current State:** Translation currently requires manual invocation via hotkey (`T`).
  * **Enhancement:** Add an optional auto-translate background detector. Monitor frame differences (using perceptual hashing, SSIM via OpenCV, or VRAM tilemap dirty tracking) on text regions. Automatically queue an AI translation without user input when a dialogue box advances, with a debounce timer to avoid API throttling.
- [ ] 2. **In-Place Visual Inpainting / Direct Textbox Replacement**

  * **Current State:** Translations display in the bottom dock or as a fullscreen floating subtitle card.
  * **Enhancement:** Use OCR bounding box coordinates `(x, y, w, h)` to render matching background patches directly over the Japanese dialogue bubble in `VideoGLWidget` and draw the localized text inside the game's actual textbox.
- [ ] 3. **Text-To-Speech (TTS) Game Dubbing**

  * **Current State:** Dialogue is visual-only.
  * **Enhancement:** Integrate lightweight offline or streaming TTS (`edge-tts` or `piper-tts`) to vocalize translated text upon detection. Automatically duck emulator audio volume during voice playback.
- [ ] 4. **One-Click "Translate to ROM" (Direct Text Insertion Workflow)**

  * **Current State:** Script extraction, repointing, and text search require navigating several tabs in `TranslationToolsDialog`.
  * **Enhancement:** Add an *"Inject to ROM"* action directly in the translation subtitle panel. Automatically find the active on-screen string in the ROM via relative/table search, repoint its pointer, and write the localized text directly to a patched `.gba` file.

---

### 2. ROM Hacking & Forensic Dev Tools

- [X] 5. **Interactive GBA Tilemap (SBB) Editor (AdvanceMap Lite)**

  * **Current State:** `TileViewerDialog` and `MapViewerDialog` are read-only inspectors; only `GraphicEditorDialog` allows drawing raw tile pixels.
  * **Enhancement:** Enable interactive tile placement in `MapViewerDialog`. Selecting a tile from the pattern table acts as a brush to click and stamp tile indices and palette banks directly onto the 32×32 Screen Base Block in live VRAM, with IPS/ROM patch export support.
- [X] 6. **Audio Soundbank & Sappy (M4A) Music Extractor**

  * **Current State:** Audio can only be recorded via the clip recorder or listened to in real-time.
  * **Enhancement:** Scan ROM data for standard Nintendo GBA Sappy / M4A sound engine tables (`song_table` pointers). Enable playing individual music sequences and exporting them to standard `.mid` (MIDI), `.wav`, and `.sf2` soundfonts.
- [X] 7. **Execution & Write Watchpoints / Breakpoints**

  * **Current State:** Memory inspection is passive or polled at fixed timer intervals.
  * **Enhancement:** Allow setting memory write watchpoints on EWRAM/IWRAM addresses via `HexEditorDialog` or `MemorySearchDialog`. Automatically pause emulation and display the calling instruction (via Capstone) whenever a targeted variable (e.g., player HP) is modified.
- [X] 8. **Live Memory Freeze & Watch Table (Cheat Engine "Address Table" View)**

  * **Current State:** Cheats can be added to freeze memory, but arbitrary addresses cannot be monitored side-by-side in real time.
  * **Enhancement:** Add a persistent "Watch Table" panel inside the Hex Editor or Cheat Engine. Pin arbitrary memory addresses with custom labels and display types (`uint8`, `uint16`, `uint32`, `float`, `hex`, `string`), with live-refresh readouts and toggleable freeze locks.

---

### 3. Core Emulation & TAS / Tool-Assisted Features

- [X] 9. **Piano-Roll TAS Input Timeline Editor**

  * **Current State:** Replay recording saves deterministic `.replay.json` files, but inputs cannot be graphically edited frame-by-frame.
  * **Enhancement:** Build a visual column-based TAS timeline editor (BizHawk/FCEUX style). Display frames on the vertical axis and controller buttons (`A`, `B`, `L`, `R`, `D-Pad`, etc.) as clickable grid cells to toggle inputs, step backward/forward, and test frame-perfect actions.
- [X] 10. **Time-Stretching / Pitch-Corrected Fast-Forward Audio**

  * **Current State:** Fast-forwarding currently raises sound pitch or skips audio buffer writes.
  * **Enhancement:** Integrate a WSOLA/SoundTouch time-stretching filter in `audio_dsp.py` so playing at `1.5x` or `2.0x` speeds up the music and sound effects without high-pitched distortion.
- [X] 11. **GBA Local Netplay / Wireless Link Cable Emulation**

  * **Current State:** Single-player emulation only.
  * **Enhancement:** Utilize mGBA's native serial/link cable communication via local network sockets or a dual-core worker instance, enabling Pokémon trading/battling and multi-player GBA titles.

---

### 4. UI, Presentation & Retro Aesthetics

- [ ] 12. **Custom Handheld Bezel & Artwork Borders**

  * **Current State:** Fullscreen mode on 16:9/16:10 monitors displays black pillarboxes on the sides.
  * **Enhancement:** Add selectable authentic GBA handheld bezel overlays (Original Indigo GBA, GBA SP, Game Boy Micro, or Game Boy Player frame) filling the unused border space around the 3:2 screen.
- [ ] 13. **Visual ROM Library & Box Art Launcher Grid**

  * **Current State:** Games are loaded exclusively through file dialogs or the recent files menu.
  * **Enhancement:** Add a visual grid launcher view when no game is running, displaying scanned games from a `roms/` folder with scraped box art, game title, publisher, and cartridge metadata.
- [ ] 14. **Per-Game Playtime & Statistics Tracking**

  * **Current State:** Per-game profiles save configuration settings, but gameplay statistics are not recorded.
  * **Enhancement:** Track and persist `playtime_seconds`, `launch_count`, and `last_played` timestamps inside each game's `profiles/<game_code>.json`, displaying stats in the status HUD or launcher.

---

### Quick Priority Pick

1. **Auto-Translate on Scene Change (`#1`)** *(Massive quality-of-life upgrade for the AI translator)*
2. **Live Memory Freeze & Watch Table (`#8`)** *(Turns the Hex Editor into a true Cheat Engine)*
3. **Bezel / Handheld Frame Artwork in Fullscreen (`#12`)** *(Instant commercial polish to fullscreen mode)*

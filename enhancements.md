Based on your current codebase (`core_worker.py`, `gui.py`, `dev_tools.py`, `graphic_editor.py`, `hex_editor.py`, `shader_manager.py`, `translation_tools.py`, etc.), you already have an impressive emulator + ROM hacking/AI translation workstation.

Here are the highest-impact improvements, grouped from **critical core gaps** to **new features and polish** tailored to your exact code:

---

### 1. Core Emulation & Quality-of-Life Gaps (High Impact)

- [X] ~~1. **Automatic Battery Save (`.sav`) Persistence**~~ *(Implemented in `state_storage.py` & `core_worker.py`)*

  * ~~**Current Gap:** `config.py` defines `"saves": "saves"`, and `_libretro_environ_cb` passes it to mGBA, **but neither `core_worker.py` nor `gui.py` actually reads/writes `MemoryRegion.SAVE_RAM` (`SRAM`) to disk**. In `pdretro`, in-game saves (like Pokémon save files) can be lost when closing the emulator unless you use Save States.~~
  * ~~**Enhancement:** On ROM load, check for `saves/<rom_name>.sav` and inject it via `write_memory_region("SRAM", 0, data)`. Periodically hash `SRAM` (e.g., every 2 seconds) and on `stop()`, flush changes back to `.sav` automatically.~~
- [X] ~~2. **Fix Live Audio Mute / Unmute (`core_worker.py` & `audio_dsp.py`)**~~ *(Implemented in `audio_dsp.py` & `core_worker.py`)*

  * ~~**Current Gap:** In `core_worker.py`, `sounddevice.OutputStream` is only created at startup if `mute` is `False`. Toggling **Audio → Mute** while playing updates `AudioDSP`, which doesn't check `mute`, so toggling Mute on/off mid-game does nothing until you reload the ROM.~~
  * ~~**Enhancement:** Always initialize the stream (or manage it cleanly), and add `self.muted = bool(self.config.get("audio", "mute"))` inside `AudioDSP.update_settings()` to output zeros (or skip `stream.write()`) while keeping clock pacing smooth.~~
- [X] ~~3. **Real-Time Rewind Buffer (`Space` / `` ` `` / Hotkey)**~~ *(Implemented in `core_worker.py`, `config.py`, `main_window.py`, `menu_actions.py`, `application_actions.py`, & `runtime_actions.py`)*

  * ~~**Why it fits cleanly:** You already have fast in-memory state serialization via `_ra_wrapper.serialize_state()` and `unserialize_state()`.~~
  * ~~**Enhancement:** Push `zlib`-compressed state snapshots into a `collections.deque(maxlen=300)` every ~10 frames (~50 seconds of rewind history in ~20–30 MB of RAM). Holding a Rewind key pauses normal execution and steps backward through the deque.~~
- [ ] 4. **Automatic Soft-Patching on Load (`.ips` / `.ups` / `.bps`)**

  * **Why it fits cleanly:** `graphic_editor.py` already generates `.ips` patches, and your emulator is heavily focused on ROM translation (`translation_tools.py`).
  * **Enhancement:** When loading `Game.gba`, automatically check for `Game.ips` / `Game.bps` / `Game.ups` (or let the user pick a patch) and apply it to an in-memory/temp ROM buffer without modifying the original clean ROM on disk.
- [ ] 5. **Visual Save State Slot Browser (With PNG Thumbnails)**

  * **Enhancement:** Whenever `quick_save_state(slot)` runs, save `latest_video_frame` alongside `.ss1` as `.ss1.png` (plus timestamp). Add a **Save State Manager** dialog showing a 3×3 thumbnail grid of Slots 1–9 so you never accidentally overwrite the wrong slot.

---

### 2. Dev Tools & ROM Hacking Polish (Fixing Existing Stubs)

- [X] ~~6. **Real Palette (PRAM) Rendering in Tile & Map Viewers (`dev_tools.py`)**~~ *(Implemented in `tile_map_viewers.py` & `palette_sprite_viewers.py`)*

  * ~~**Current Gap:** Right now, `TileViewerDialog.render_tiles()` and `MapViewerDialog.render_map()` both call `colors = get_default_palette()` (rainbow placeholder colors) instead of reading the actual live `PRAM` from the game, even though `PaletteViewerDialog` already parses `PRAM`!~~
  * ~~**Enhancement:** Extract `PRAM` via `bgr555_to_rgb` inside `TileViewerDialog` and `MapViewerDialog` so tiles and tilemaps render with true in-game colors.~~
- [X] ~~7. **Live Auto-Refresh Mode in Dev Viewers (`dev_tools.py`)**~~ *(Implemented in `tile_map_viewers.py` & `palette_sprite_viewers.py`)*

  * ~~**Enhancement:** `HexEditorDialog` already has a great `⚡ Live Watch` timer (`100 ms`). Adding a similar **Auto-Refresh (10 Hz)** checkbox to `PaletteViewerDialog`, `SpriteViewerDialog`, `TileViewerDialog`, and `MapViewerDialog` lets you watch VRAM tiles, palettes, and OAM sprites update live as the game plays.~~
- [ ] 8. **Undo / Redo History in `GraphicEditorDialog` (`graphic_editor.py`)**

  * **Current Gap:** `HexEditorDialog` has `undo_stack` and `redo_stack` (`Ctrl+Z` / `Ctrl+Y`), but the YY-CHR `GraphicEditorDialog` writes straight into `self.vram_copy` with no undo history.
  * **Enhancement:** Record `(offset, old_byte, new_byte)` actions per stroke/fill so `Ctrl+Z` / `Ctrl+Y` works while drawing sprites or tiles.
- [X] ~~9. **ZIP**~~ / **7z Archive ROM Loading** *(Implemented `.zip` loading in `runtime_actions.py`; `.7z` not yet added)*

  * ~~**Current Gap:** `load_rom()` in `gui.py` includes `*.zip` in the file filter, but `pdretro` / `Emulator.load_game()` generally expects an uncompressed `.gba` path (and `_parse_rom_header` seeks `0xAC` directly on the file).~~
  * ~~**Enhancement:** Use Python's built-in `zipfile` module in `load_rom_file()` to extract `.gba`/`.gb`/`.gbc` files transparently into a temporary cache folder before booting.~~

---

### 3. Video, Shaders & UI Enhancements

- [X] ~~10. **Save & Restore Custom Shader Chains & Parameters**~~ *(Implemented in `config.py`, `shader_types.py`, `shader_pipeline.py`, `shader_dialog.py`, & `main_window.py`)*

  * ~~**Current Gap:** Currently `mgba_config.json` saves `shaders.enabled` and `shaders.preset` (e.g., `"gba_color"`), so if you prepend/append custom `.glsl` passes or tweak parameter sliders in `ShaderConfigDialog`, those tweaks reset on restart.~~
  * ~~**Enhancement:** Auto-save the active shader chain to `shaders/autosave.glslp` (or store pass paths + parameter dicts inside `mgba_config.json`) and restore them automatically on boot.~~
- [X] ~~11. **Fullscreen Mode (`Alt+Enter` / `F11`) & Integer Scaling Option**~~ *(Implemented in `shader_pipeline.py`, `video_widget.py`, `menu_actions.py`, `application_actions.py`, `settings_actions.py`, & `main_window.py`)*

  * ~~**Enhancement:** Add a clean borderless fullscreen toggle that temporarily hides the menu bar, HUD, and translation panel, plus an **Integer Scale Only** toggle inside `VideoGLWidget` so pixels snap strictly to 1×, 2×, 3×, etc. without shimmer even in maximized windows.~~
- [ ] 12. **Drag-and-Drop Support**

  * **Enhancement:** Enable `setAcceptDrops(True)` on `MainWindow` so dragging a `.gba` ROM, `.state` save state, `.glslp` shader preset, or `.replay.json` onto the window immediately loads it.
- [ ] 13. **Built-In Animated GIF / MP4 Clip Recorder**

  * **Why it fits cleanly:** `Pillow` and `opencv-python` are already in `requirements.txt`.
  * **Enhancement:** Add a **"Record GIF / Video Clip"** toggle in the Video menu that buffers `latest_video_frame` at 30/60 FPS and exports a crisp pixel-art GIF or MP4 to `exports/`.
- [ ] 14. **Per-Game Configuration Profiles**

  * **Enhancement:** Use `self.game_code` (like `BPPE`, `V49E`, `AXBJ`) to save per-game overrides (e.g., different controller layouts, rumble strength, shader presets, or Boktai solar levels per game).

---

### 4. AI Translation & Replay Ecosystem

- [ ] 15. **On-Screen Floating Subtitle Overlay**

  * **Enhancement:** Alongside the bottom `translation_panel` dock, add an option (`ai_translation.overlay_enabled` is already in `config.py`!) to draw translated dialogue boxes directly onto `VideoGLWidget` via `QPainter` (either at the bottom of the frame or anchored to the detected OCR bounding boxes), which is especially useful in Fullscreen mode.
- [ ] 16. **Translation Cache & Per-Game Glossary**

  * **Enhancement:** Cache `(source_text -> translated_text)` per ROM in `translations/<game_code>_cache.json` + editable character/term glossary so repeated menu prompts (`"はい / いいえ"`, `"セーブ"`) translate instantaneously without calling the LLM every time, and character names stay consistent.
- [ ] 17. **Cheat Engine Code Validation & Persistence (`cheats.py`)**

  * **Enhancement:** Ensure cheats added from `MemorySearchDialog` or `CheatManagerWindow` auto-save to `cheats/<game_name>.cht` and auto-load on ROM boot, with support for GameShark v1/v3, Action Replay, and CodeBreaker decryption.
- [ ] 18. **Live Input Overlay / TAS Frame Counter HUD**

  * **Enhancement:** When `SessionRecorder` is recording or playing back, show a small frame counter + live pressed-button indicator (`[A] [B] [▲▼◀▶]`) on the HUD or OSD—great for debugging replays and speedruns.

---

### Quick Pick — Completed

- [X] ~~1. **`.sav` SRAM persistence + `.zip` ROM loading + Live Mute fix** (fixes real gameplay persistence & audio bugs).~~
- [X] ~~2. **Real `PRAM` colors & Auto-Refresh in `TileViewer` / `MapViewer`** (instant upgrade to your dev tools).~~
- [X] ~~3. **In-Memory Rewind System** + **Shader Parameter Persistence + Fullscreen Mode**.~~

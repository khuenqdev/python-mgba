# scene_dump.py
import os
import csv
import json
import struct
import hashlib
import datetime
import numpy as np
from PIL import Image

from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QFileDialog, 
                             QMessageBox, QProgressBar)
from PyQt6.QtCore import Qt

try:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
    CAPSTONE_AVAILABLE = True
except ImportError:
    CAPSTONE_AVAILABLE = False


def bgr555_to_rgb(val: int):
    val = int(val)
    r = (val & 0x1F) * 255 // 31
    g = ((val >> 5) & 0x1F) * 255 // 31
    b = ((val >> 10) & 0x1F) * 255 // 31
    return (int(r), int(g), int(b))


class SceneDumpEngine:
    """Extracts, analyzes, and bundles complete GBA screen forensics and state snapshots."""
    def __init__(self, worker, config):
        self.worker = worker
        self.config = config

    def create_compact_dump(self, rom_path: str) -> str:
        """Creates a forensic snapshot containing images, scene_data.json, and the exact .state file."""
        if not rom_path:
            return None

        # 1. Target directory: exports/<Game_Name>/dump_<Timestamp>/
        game_name = os.path.splitext(os.path.basename(rom_path))[0]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        base_export = self.config.get("paths", "exports") or "exports"
        dump_folder = os.path.join(base_export, game_name, f"dump_{timestamp}")
        layers_folder = os.path.join(dump_folder, "layers")
        
        os.makedirs(dump_folder, exist_ok=True)
        os.makedirs(layers_folder, exist_ok=True)

        # 2. Capture and Export the Exact Save State for this Frame
        state_filename = f"{game_name}_{timestamp}.state"
        state_path = os.path.join(dump_folder, state_filename)
        state_saved = False
        if self.worker and hasattr(self.worker, 'save_state'):
            state_saved = self.worker.save_state(state_path)

        # 3. Extract Memory Buffers
        vram = self.worker.get_memory_region("VRAM") or bytes(96 * 1024)
        pram = self.worker.get_memory_region("PRAM") or bytes(1024)
        oam = self.worker.get_memory_region("OAM") or bytes(1024)

        # 4. Extract Palettes
        bg_colors = []
        obj_colors = []
        if len(pram) >= 1024 and not all(b == 0 for b in pram[:1024]):
            words = struct.unpack("<512H", pram[:1024])
            bg_colors = [bgr555_to_rgb(w) for w in words[:256]]
            obj_colors = [bgr555_to_rgb(w) for w in words[256:]]
        else:
            frame = getattr(self.worker, 'latest_video_frame', None)
            if frame is not None:
                flat = frame.reshape(-1, 3)
                unique = np.unique(flat, axis=0)
                sampled = [(int(c[0]), int(c[1]), int(c[2])) for c in unique[:512]]
                while len(sampled) < 512: sampled.append((0, 0, 0))
                bg_colors = sampled[:256]
                obj_colors = sampled[256:]
            else:
                bg_colors = [(i, i, i) for i in range(256)]
                obj_colors = [(i, i, i) for i in range(256)]

        # 5. Save Composited Main Screen Capture
        raw_frame = getattr(self.worker, 'latest_video_frame', None)
        if raw_frame is not None:
            Image.fromarray(raw_frame, 'RGB').save(os.path.join(dump_folder, "screen.png"))

        # 6. Extract Only Meaningful Non-Empty Layer Data
        active_sbbs = self._detect_sbbs(vram)
        all_layer_tiles = {}
        layer_summaries = {}

        for bg_idx, bg_name in enumerate(["BG0", "BG1", "BG2", "BG3"]):
            sbb = active_sbbs[bg_idx] if bg_idx < len(active_sbbs) else (28 + bg_idx)
            cbb = bg_idx % 4

            layer_img, tile_data_list = self._render_and_map_bg(vram, bg_colors, sbb, cbb)
            non_empty_tiles = [t for t in tile_data_list if t["tile_dec"] != 0]

            if non_empty_tiles:
                layer_img.save(os.path.join(layers_folder, f"{bg_name.lower()}.png"))
                all_layer_tiles[bg_name] = non_empty_tiles
                layer_summaries[bg_name] = {
                    "sbb": sbb, 
                    "cbb": cbb,
                    "non_empty_tiles": len(non_empty_tiles)
                }

        # 7. Extract Visible Hardware Sprites (OAM)
        obj_img, sprite_list = self._render_and_map_sprites(vram, oam, obj_colors)
        visible_sprites = [s for s in sprite_list if s["active"]]
        if visible_sprites:
            obj_img.save(os.path.join(layers_folder, "obj.png"))

        # 8. Disassemble Rendering & DMA Routines from ROM
        asm_findings = self._disassemble_game_code()

        # 9. Compute ROM SHA1 Hash
        rom_sha1 = "unknown"
        if os.path.exists(rom_path):
            try:
                with open(rom_path, "rb") as rf:
                    rom_sha1 = hashlib.sha1(rf.read()).hexdigest()
            except Exception:
                pass

        # 10. Export Self-Contained scene_data.json and Factual scene_description.md
        self._export_forensic_files(
            dump_folder, game_name, timestamp, rom_path, rom_sha1,
            state_filename, state_saved, layer_summaries, 
            all_layer_tiles, visible_sprites, asm_findings
        )

        return dump_folder

    def _export_forensic_files(self, dump_folder, game_name, timestamp, rom_path, rom_sha1,
                               state_filename, state_saved, layer_summaries, 
                               all_layer_tiles, visible_sprites, asm_findings):
        """Writes comprehensive scene_data.json and factual scene_description.md."""
        
        # Group non-empty tiles into horizontal text lines
        text_lines = []
        for layer_name, tiles in all_layer_tiles.items():
            rows = {}
            for t in tiles:
                rows.setdefault(t["row"], []).append(t)

            for r, row_tiles in rows.items():
                if len(row_tiles) >= 2:
                    sorted_row = sorted(row_tiles, key=lambda it: it["col"])
                    hex_seq = " ".join(t["tile_hex"] for t in sorted_row)
                    text_lines.append({
                        "layer": layer_name,
                        "row": r,
                        "y_px": r * 8,
                        "start_x_px": sorted_row[0]["x"],
                        "count": len(sorted_row),
                        "hex_sequence": hex_seq,
                        "vram_start": sorted_row[0]["vram_addr"]
                    })

        # =====================================================================
        # 1. Fully Self-Contained scene_data.json
        # =====================================================================
        comprehensive_data = {
            "game": game_name,
            "rom_file": os.path.basename(rom_path),
            "rom_sha1": rom_sha1,
            "timestamp": timestamp,
            "resolution": "240x160",
            "save_state": {
                "file": state_filename,
                "saved_successfully": state_saved,
                "path_relative": state_filename
            },
            "active_layers": layer_summaries,
            "detected_text_lines": text_lines,
            "visible_sprites": [
                {
                    "id": s["id"], 
                    "pos": [s["x"], s["y"]],
                    "size": f"{s['width']}x{s['height']}",
                    "tile": s["tile_hex"], 
                    "pal": s["pal_bank"],
                    "priority": s.get("priority", 0)
                }
                for s in visible_sprites
            ],
            "disassembly": asm_findings
        }

        with open(os.path.join(dump_folder, "scene_data.json"), "w", encoding="utf-8") as f:
            json.dump(comprehensive_data, f, indent=2)

        # =====================================================================
        # 2. Factual scene_description.md (Strict Technical Report, No Prompting)
        # =====================================================================
        md_text = f"""# GBA Screen Forensic Analysis

## 1. Frame Metadata
- **Game Title**: `{game_name}`
- **ROM File**: `{os.path.basename(rom_path)}`
- **ROM SHA1**: `{rom_sha1}`
- **Captured Timestamp**: `{timestamp}`
- **Resolution**: 240×160 pixels
- **Save State Snapshot**: `{state_filename}` (Loads this exact emulation state)
- **Visual Assets**:
  - `screen.png`: Full composited game screen
  - `layers/`: Isolated active background layers and hardware sprites

---

## 2. Detected On-Screen Text & Tile Sequences
Horizontal tile sequences detected across active backgrounds:

| Layer | Row (Y) | Start X | Tile Count | Tile ID Sequence (Hex) | VRAM Start Addr |
| :--- | :--- | :--- | :--- | :--- | :--- |
"""
        for line in text_lines[:25]:
            md_text += f"| {line['layer']} | Row {line['row']} ({line['y_px']}px) | {line['start_x_px']}px | {line['count']} | `{line['hex_sequence']}` | `{line['vram_start']}` |\n"

        md_text += f"""
---

## 3. Visible Hardware Sprites (OAM)
Total visible active sprites: **{len(visible_sprites)}**

| Sprite ID | Position (X, Y) | Dimensions | Base Tile | Palette Bank | Priority |
| :--- | :--- | :--- | :--- | :--- | :--- |
"""
        for s in visible_sprites[:20]:
            md_text += f"| #{s['id']:03d} | ({s['x']}, {s['y']}) | {s['width']}×{s['height']} | `{s['tile_hex']}` | Bank {s['pal_bank']} | {s.get('priority', 0)} |\n"

        md_text += """
---

## 4. Hardware Disassembly (VRAM / DMA Subroutines)
```arm
"""
        if asm_findings.get("vram_routines"):
            for i, blk in enumerate(asm_findings["vram_routines"][:2]):
                md_text += f"; --- VRAM Transfer Subroutine #{i+1} ---\n"
                md_text += "\n".join(blk) + "\n\n"
        else:
            md_text += "; No direct VRAM routines scanned.\n"

        if asm_findings.get("dma_routines"):
            md_text += "; --- DMA Controller Subroutines ---\n"
            for blk in asm_findings["dma_routines"][:1]:
                md_text += "\n".join(blk) + "\n\n"

        md_text += "```\n"

        with open(os.path.join(dump_folder, "scene_description.md"), "w", encoding="utf-8") as f:
            f.write(md_text)

    def _detect_sbbs(self, vram):
        active = []
        for sbb in range(32):
            chunk = vram[sbb * 0x800:(sbb + 1) * 0x800]
            if any(chunk):
                words = struct.unpack("<1024H", chunk)
                if len(set(w & 0x3FF for w in words)) > 3:
                    active.append(sbb)
        return active

    def _render_and_map_bg(self, vram, bg_colors, sbb, cbb):
        img = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
        tile_data_list = []
        sbb_offset = sbb * 0x800
        cbb_offset = cbb * 0x4000
        if sbb_offset + 0x800 > len(vram): return img, tile_data_list

        entries = struct.unpack("<1024H", vram[sbb_offset:sbb_offset + 0x800])

        for ty in range(20):
            for tx in range(30):
                entry = entries[ty * 32 + tx]
                tile_idx = entry & 0x3FF
                h_flip = bool(entry & (1 << 10))
                v_flip = bool(entry & (1 << 11))
                pal_bank = (entry >> 12) & 0xF

                tile_vram_addr = 0x06000000 + cbb_offset + (tile_idx * 32)
                tile_data_list.append({
                    "row": ty, "col": tx, "x": tx * 8, "y": ty * 8,
                    "tile_dec": tile_idx, "tile_hex": f"0x{tile_idx:03X}",
                    "vram_addr": f"0x{tile_vram_addr:08X}",
                    "pal_bank": pal_bank, "h_flip": h_flip, "v_flip": v_flip
                })

                t_addr = cbb_offset + (tile_idx * 32)
                if t_addr + 32 > len(vram): continue
                tile_bytes = vram[t_addr:t_addr + 32]

                for py in range(8):
                    for px in range(8):
                        actual_px = (7 - px) if h_flip else px
                        actual_py = (7 - py) if v_flip else py

                        b = tile_bytes[actual_py * 4 + actual_px // 2]
                        c_idx = (b & 0xF) if (actual_px % 2 == 0) else ((b >> 4) & 0xF)
                        if c_idx != 0:
                            rgb = bg_colors[(pal_bank * 16 + c_idx) % len(bg_colors)]
                            img.putpixel((tx * 8 + px, ty * 8 + py), (*rgb, 255))

        return img, tile_data_list

    def _render_and_map_sprites(self, vram, oam, obj_colors):
        img = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
        sprite_list = []
        if len(oam) < 1024: return img, sprite_list

        words = struct.unpack("<512H", oam[:1024])
        SPRITE_SIZES = {
            0: [(8, 8), (16, 16), (32, 32), (64, 64)],
            1: [(16, 8), (32, 8), (32, 16), (64, 32)],
            2: [(8, 16), (8, 32), (16, 32), (32, 64)]
        }

        for i in range(128):
            a0, a1, a2 = words[i*4], words[i*4+1], words[i*4+2]
            y = a0 & 0xFF
            shape = (a0 >> 14) & 0x3
            x = a1 & 0x1FF
            if x >= 240: x -= 512
            size_idx = (a1 >> 14) & 0x3
            w, h = SPRITE_SIZES.get(shape, [(8, 8)]*4)[size_idx]
            tile = a2 & 0x3FF
            pal = (a2 >> 12) & 0xF
            h_flip = bool(a1 & (1 << 12))
            v_flip = bool(a1 & (1 << 13))
            pri = (a2 >> 10) & 0x3
            is_active = (y < 160 and x < 240 and x + w > 0)

            sprite_list.append({
                "id": i, "active": is_active, "x": x, "y": y,
                "width": w, "height": h, "tile_dec": tile,
                "tile_hex": f"0x{tile:03X}", "pal_bank": pal,
                "priority": pri, "h_flip": h_flip, "v_flip": v_flip
            })

            if not is_active: continue

            tx_count, ty_count = w // 8, h // 8
            for ty in range(ty_count):
                for tx in range(tx_count):
                    t_idx = tile + (ty * 32) + tx
                    t_addr = 0x10000 + (t_idx * 32)
                    if t_addr + 32 > len(vram): continue
                    tile_bytes = vram[t_addr:t_addr + 32]

                    for py in range(8):
                        for px in range(8):
                            actual_px = (w - 1 - (tx * 8 + px)) if h_flip else (tx * 8 + px)
                            actual_py = (h - 1 - (ty * 8 + py)) if v_flip else (ty * 8 + py)

                            if not (0 <= x + actual_px < 240 and 0 <= y + actual_py < 160):
                                continue

                            b = tile_bytes[py * 4 + px // 2]
                            c_idx = (b & 0xF) if (px % 2 == 0) else ((b >> 4) & 0xF)
                            if c_idx != 0:
                                rgb = obj_colors[(pal * 16 + c_idx) % len(obj_colors)]
                                img.putpixel((x + actual_px, y + actual_py), (*rgb, 255))

        return img, sprite_list

    def _disassemble_game_code(self) -> dict:
        findings = {
            "entry_point": [],
            "vram_routines": [],
            "dma_routines": [],
            "capstone_installed": CAPSTONE_AVAILABLE
        }
        if not self.worker or not self.worker.rom_path or not os.path.exists(self.worker.rom_path) or not CAPSTONE_AVAILABLE:
            return findings

        with open(self.worker.rom_path, "rb") as f:
            rom_data = f.read(min(1024 * 1024, os.path.getsize(self.worker.rom_path)))

        cs_thumb = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        cs_arm = Cs(CS_ARCH_ARM, CS_MODE_ARM)

        try:
            for insn in cs_arm.disasm(rom_data[0:64], 0x08000000):
                findings["entry_point"].append(f"{insn.address:#010x}:  {insn.mnemonic:<8} {insn.op_str}")
        except Exception: pass

        vram_pattern = struct.pack("<I", 0x06000000)
        pos = 0
        while len(findings["vram_routines"]) < 3:
            found = rom_data.find(vram_pattern, pos)
            if found == -1: break
            fn_start = max(0, found - 20)
            addr = 0x08000000 + fn_start
            routine_block = []
            for insn in cs_thumb.disasm(rom_data[fn_start:fn_start + 40], addr):
                routine_block.append(f"{insn.address:#010x}:  {insn.mnemonic:<8} {insn.op_str}")
            if routine_block: findings["vram_routines"].append(routine_block)
            pos = found + 4

        dma_pattern = struct.pack("<I", 0x040000B0)
        found_dma = rom_data.find(dma_pattern)
        if found_dma != -1:
            fn_start = max(0, found_dma - 16)
            addr = 0x08000000 + fn_start
            routine_block = []
            for insn in cs_thumb.disasm(rom_data[fn_start:fn_start + 48], addr):
                routine_block.append(f"{insn.address:#010x}:  {insn.mnemonic:<8} {insn.op_str}")
            findings["dma_routines"].append(routine_block)

        return findings



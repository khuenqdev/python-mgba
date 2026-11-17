# sappy_extractor.py
import os
import struct
import wave
import threading
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QLineEdit, QSpinBox, QComboBox, QTabWidget, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

# Standard GBA M4A / Sappy 49-entry wait/note-length table (24 PPQN)
M4A_WAIT_TABLE = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
    13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
    28, 30, 32, 36, 40, 42, 44, 48, 52, 54, 56, 60,
    64, 66, 68, 72, 76, 78, 80, 84, 88, 90, 92, 96,
]


def _is_rom_ptr(val: int, rom_len: int) -> bool:
    return 0x08000000 <= val < (0x08000000 + rom_len)


def _ptr_to_off(val: int) -> int:
    return val - 0x08000000


def _validate_song_header(rom_data: bytes, hdr_off: int) -> dict | None:
    rom_len = len(rom_data)
    if hdr_off < 0 or hdr_off + 8 > rom_len:
        return None
    track_count = rom_data[hdr_off]
    block_count = rom_data[hdr_off + 1]
    priority = rom_data[hdr_off + 2]
    reverb = rom_data[hdr_off + 3]
    vg_ptr = struct.unpack_from("<I", rom_data, hdr_off + 4)[0]

    # Standard M4A dummy song header (`mus_dummy`: {0, 0, 0, 0, 0})
    if track_count == 0:
        if vg_ptr == 0 or _is_rom_ptr(vg_ptr, rom_len):
            return {
                "is_dummy": True,
                "header_offset": hdr_off,
                "track_count": 0,
                "block_count": block_count,
                "priority": priority,
                "reverb": reverb,
                "voicegroup_ptr": vg_ptr,
                "track_ptrs": [],
            }
        return None

    if track_count > 16 or block_count > 64:
        return None
    if (vg_ptr & 3) != 0 or not _is_rom_ptr(vg_ptr, rom_len):
        return None
    if hdr_off + 8 + (track_count * 4) > rom_len:
        return None

    track_ptrs = []
    for t in range(track_count):
        tp = struct.unpack_from("<I", rom_data, hdr_off + 8 + (t * 4))[0]
        if not _is_rom_ptr(tp, rom_len):
            return None
        track_ptrs.append(tp)

    return {
        "is_dummy": False,
        "header_offset": hdr_off,
        "track_count": track_count,
        "block_count": block_count,
        "priority": priority,
        "reverb": reverb,
        "voicegroup_ptr": vg_ptr,
        "track_ptrs": track_ptrs,
    }


def _try_parse_song_table_at(rom_data: bytes, start_off: int, max_entries: int = 1024) -> list:
    """Attempts to parse an M4A song table starting at a 4-byte aligned ROM offset."""
    rom_len = len(rom_data)
    if start_off < 0 or (start_off & 3) != 0 or start_off + 8 > rom_len:
        return []

    songs = []
    unique_headers = set()
    zero_streak = 0

    for k in range(max_entries):
        pos = start_off + (k * 8)
        if pos + 8 > rom_len:
            break
        hdr_ptr, grp_word = struct.unpack_from("<II", rom_data, pos)
        ms = grp_word & 0xFFFF
        me = (grp_word >> 16) & 0xFFFF

        if hdr_ptr == 0 and grp_word == 0:
            zero_streak += 1
            if zero_streak > 4:
                break
            continue

        if ms >= 32 or me >= 32:
            break
        if (hdr_ptr & 3) != 0 or not _is_rom_ptr(hdr_ptr, rom_len):
            break

        zero_streak = 0
        parsed_hdr = _validate_song_header(rom_data, _ptr_to_off(hdr_ptr))
        if parsed_hdr is None:
            break

        if not parsed_hdr.get("is_dummy"):
            unique_headers.add(parsed_hdr["header_offset"])
            songs.append({
                "song_id": k,
                "entry_offset": pos,
                "player_group": ms,
                **parsed_hdr,
            })

    if len(songs) >= 3 and len(unique_headers) >= 2:
        return songs
    return []


def _find_tables_via_thumb_code(rom_data: bytes) -> list:
    """Finds M4A song tables via `m4aSongNumStart` / `SelectSong` Thumb literal pools."""
    rom_len = len(rom_data)
    found_offsets = set()
    for sig in (b"\x00\x04\x04\x0C", b"\x00\x04\x00\x0C"):
        pos = 0
        while True:
            idx = rom_data.find(sig, pos)
            if idx == -1:
                break
            pos = idx + 2
            win_end = min(rom_len - 2, idx + 64)
            for pc_off in range(idx, win_end, 2):
                insn = struct.unpack_from("<H", rom_data, pc_off)[0]
                if (insn & 0xF800) == 0x4800:
                    imm = (insn & 0xFF) * 4
                    pool_off = ((pc_off + 4) & ~3) + imm
                    if pool_off + 4 <= rom_len:
                        ptr = struct.unpack_from("<I", rom_data, pool_off)[0]
                        if (ptr & 3) == 0 and _is_rom_ptr(ptr, rom_len):
                            found_offsets.add(_ptr_to_off(ptr))

    tables = []
    for off in sorted(found_offsets):
        songs = _try_parse_song_table_at(rom_data, off)
        if songs:
            tables.append({"table_offset": off, "songs": songs})
    return tables


def scan_sappy_song_tables(rom_data: bytes) -> list:
    """
    Scans a GBA ROM for Nintendo M4A / Sappy `song_table` structures across
    both 4-byte alignment phases (offset % 8 == 0 and 4), allowing dummy slots.
    """
    rom_len = len(rom_data)
    if rom_len < 0x200:
        return []

    tables = []
    seen_offsets = set()

    for t in _find_tables_via_thumb_code(rom_data):
        off = t["table_offset"]
        if off not in seen_offsets:
            seen_offsets.add(off)
            tables.append(t)

    words = np.frombuffer(rom_data[:rom_len - (rom_len % 4)], dtype="<u4")
    for phase in (0, 1):
        sub = words[phase:]
        pair_count = len(sub) // 2
        if pair_count < 6:
            continue

        hdr_words = sub[:pair_count * 2:2]
        grp_words = sub[1:pair_count * 2:2]
        ms_words = grp_words & 0xFFFF
        me_words = (grp_words >> 16) & 0xFFFF

        valid_entry = (
            (hdr_words >= 0x080000C0)
            & (hdr_words < 0x08000000 + rom_len)
            & ((hdr_words & 3) == 0)
            & (ms_words < 32)
            & (me_words < 32)
        )

        i = 0
        while i < pair_count:
            if not valid_entry[i]:
                i += 1
                continue

            j = i
            while j < pair_count and valid_entry[j]:
                j += 1

            run_len = j - i
            if run_len >= 4:
                table_off = (phase + i * 2) * 4
                if table_off not in seen_offsets:
                    songs = _try_parse_song_table_at(rom_data, table_off, max_entries=max(run_len + 16, 512))
                    if songs:
                        seen_offsets.add(table_off)
                        tables.append({"table_offset": table_off, "songs": songs})
                        last_off = songs[-1]["entry_offset"]
                        i = max(i + 1, ((last_off - phase * 4) // 8) + 1)
                        continue
            i += 1

    tables.sort(key=lambda t: (len(t["songs"]), -t["table_offset"]), reverse=True)
    filtered = []
    covered_ranges = []
    for t in tables:
        start = t["table_offset"]
        end = t["songs"][-1]["entry_offset"] + 8
        if any(cs <= start < ce and end <= ce for cs, ce in covered_ranges):
            continue
        covered_ranges.append((start, end))
        filtered.append(t)

    return filtered


# =============================================================================
# M4A VOICEGROUP & SAMPLE DECODER
# =============================================================================
def _read_sound_sample(rom_data: bytes, smp_off: int) -> dict | None:
    rom_len = len(rom_data)
    if smp_off < 0 or smp_off + 16 > rom_len:
        return None
    w0, loop_flag, freq_q10, loop_start, num_samples = struct.unpack_from("<HHIII", rom_data, smp_off)

    if (w0 & 0xFF) not in (0x00, 0x01):
        return None
    if loop_flag not in (0x0000, 0x4000):
        return None
    if num_samples < 32 or num_samples > 1_500_000:
        return None
    sr = freq_q10 / 1024.0
    if freq_q10 == 0 or not (4000.0 <= sr <= 44100.0):
        return None
    if smp_off + 16 + num_samples > rom_len:
        return None

    pcm_i8 = np.frombuffer(rom_data, dtype=np.int8, count=num_samples, offset=smp_off + 16)
    is_loop = bool(loop_flag & 0x4000) and (loop_start < num_samples)

    return {
        "offset": smp_off,
        "sample_rate": float(sr),
        "loop": is_loop,
        "loop_start": int(loop_start) if is_loop else 0,
        "loop_end": int(num_samples),
        "pcm_i8": pcm_i8,
    }


def read_voicegroup(rom_data: bytes, vg_ptr: int, max_entries: int = 128, depth: int = 0) -> list:
    """Parses up to 128 12-byte instrument records from an M4A voicegroup."""
    rom_len = len(rom_data)
    if not _is_rom_ptr(vg_ptr, rom_len):
        return []
    base = _ptr_to_off(vg_ptr)
    entries = []

    for idx in range(max_entries):
        off = base + (idx * 12)
        if off + 12 > rom_len:
            break
        b0, b1, b2, b3, ptr1, ptr2 = struct.unpack_from("<BBBBII", rom_data, off)
        inst_type = b0 & 0xFF

        if inst_type in (0x00, 0x08) and _is_rom_ptr(ptr1, rom_len):
            smp_off = _ptr_to_off(ptr1)
            smp_info = _read_sound_sample(rom_data, smp_off)
            if smp_info:
                drum_pitch = (b1 & 0x7F) if (depth > 0 and (b1 & 0x7F) > 0) else 60
                entries.append({
                    "program": idx,
                    "kind": "pcm",
                    "fixed_freq": (inst_type == 0x08),
                    "base_key": 60,
                    "drum_key": drum_pitch,
                    "pan": b3 & 0x7F,
                    "adsr": ptr2,
                    "sample": smp_info,
                })
                continue

        if inst_type in (0x01, 0x02, 0x09, 0x0A):
            entries.append({
                "program": idx,
                "kind": "square",
                "duty": ptr1 & 0x3,
                "base_key": 60,
                "drum_key": (b1 & 0x7F) if (depth > 0 and (b1 & 0x7F) > 0) else 60,
                "adsr": ptr2,
            })
        elif inst_type in (0x03, 0x0B):
            entries.append({
                "program": idx,
                "kind": "square",
                "duty": 2,
                "base_key": 60,
                "drum_key": (b1 & 0x7F) if (depth > 0 and (b1 & 0x7F) > 0) else 60,
                "adsr": ptr2,
            })
        elif inst_type in (0x04, 0x0C):
            entries.append({
                "program": idx,
                "kind": "noise",
                "base_key": 60,
                "drum_key": (b1 & 0x7F) if (depth > 0 and (b1 & 0x7F) > 0) else 60,
                "adsr": ptr2,
            })
        elif inst_type == 0x40 and depth == 0 and _is_rom_ptr(ptr1, rom_len) and _is_rom_ptr(ptr2, rom_len):
            sub = read_voicegroup(rom_data, ptr1, max_entries=64, depth=1)
            kt_off = _ptr_to_off(ptr2)
            key_table = rom_data[kt_off:kt_off + 128] if kt_off + 128 <= rom_len else b""
            first_pcm = next((s for s in sub if s["kind"] == "pcm"), None)
            if first_pcm or sub:
                entries.append({
                    **(first_pcm or sub[0]),
                    "program": idx,
                    "kind": "keysplit",
                    "sub_entries": sub,
                    "key_table": key_table,
                })
        elif inst_type == 0x80 and depth == 0 and _is_rom_ptr(ptr1, rom_len):
            sub = read_voicegroup(rom_data, ptr1, max_entries=128, depth=1)
            entries.append({
                "program": idx,
                "kind": "drumkit",
                "sub_entries": sub,
            })

    return entries


# =============================================================================
# M4A SEQUENCE PARSER
# =============================================================================
def parse_m4a_song(rom_data: bytes, song: dict, max_loops: int = 1) -> dict:
    """Decodes an M4A song header & all its tracks into normalized note/MIDI events."""
    rom_len = len(rom_data)
    all_events = []
    all_notes = []
    tempos = [(0, 120)]
    max_tick = 0

    for t_idx, t_ptr in enumerate(song["track_ptrs"]):
        pos = _ptr_to_off(t_ptr)
        ret_stack = []
        rept_stack = {}
        goto_count = 0

        tick = 0
        voice = 0
        vol = 100
        pan = 64
        key_shift = 0
        last_cmd = 0
        last_note = 60
        last_vel = 100
        active_ties = {}

        steps = 0
        while 0 <= pos < rom_len and steps < 50000:
            steps += 1
            b = rom_data[pos]
            pos += 1

            if b < 0x80:
                if last_cmd >= 0xCE:
                    cmd = last_cmd
                    pos -= 1
                else:
                    continue
            else:
                cmd = b
                if cmd >= 0xCE:
                    last_cmd = cmd

            if 0x80 <= cmd <= 0xB0:
                tick += M4A_WAIT_TABLE[cmd - 0x80]
                continue

            if cmd == 0xB1:
                break
            elif cmd == 0xB2:
                if pos + 4 > rom_len:
                    break
                target = struct.unpack_from("<I", rom_data, pos)[0]
                pos += 4
                goto_count += 1
                if goto_count > max_loops or not _is_rom_ptr(target, rom_len):
                    break
                pos = _ptr_to_off(target)
            elif cmd == 0xB3:
                if pos + 4 > rom_len:
                    break
                target = struct.unpack_from("<I", rom_data, pos)[0]
                pos += 4
                if _is_rom_ptr(target, rom_len) and len(ret_stack) < 8:
                    ret_stack.append(pos)
                    pos = _ptr_to_off(target)
            elif cmd == 0xB4:
                if ret_stack:
                    pos = ret_stack.pop()
                else:
                    break
            elif cmd == 0xB5:
                if pos + 5 > rom_len:
                    break
                cnt = rom_data[pos]
                target = struct.unpack_from("<I", rom_data, pos + 1)[0]
                pos += 5
                cur = rept_stack.get(pos, 0) + 1
                if (cnt == 0 and cur <= max_loops) or (cnt > 0 and cur < cnt):
                    rept_stack[pos] = cur
                    if _is_rom_ptr(target, rom_len):
                        pos = _ptr_to_off(target)
                else:
                    rept_stack.pop(pos, None)
            elif cmd == 0xB9:
                if pos < rom_len and rom_data[pos] >= 6:
                    pos += 7
                else:
                    pos += 3
            elif cmd == 0xBA:
                pos += 1
            elif cmd == 0xBB:
                if pos < rom_len:
                    bpm = max(20, min(300, rom_data[pos] * 2))
                    pos += 1
                    tempos.append((tick, bpm))
                    all_events.append({"track": t_idx, "tick": tick, "type": "tempo", "bpm": bpm})
            elif cmd == 0xBC:
                if pos < rom_len:
                    key_shift = struct.unpack_from("<b", rom_data, pos)[0]
                    pos += 1
            elif cmd == 0xBD:
                if pos < rom_len:
                    voice = rom_data[pos] & 0x7F
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "program", "val": voice})
            elif cmd == 0xBE:
                if pos < rom_len:
                    vol = rom_data[pos] & 0x7F
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "cc", "cc": 7, "val": vol})
            elif cmd == 0xBF:
                if pos < rom_len:
                    pan = rom_data[pos] & 0x7F
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "cc", "cc": 10, "val": pan})
            elif cmd == 0xC0:
                if pos < rom_len:
                    bend = struct.unpack_from("<b", rom_data, pos)[0]
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "bend", "val": bend})
            elif cmd in (0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC8):
                pos += 1
            elif cmd == 0xCD:
                if pos < rom_len and rom_data[pos] == 0x08:
                    pos += 5
                else:
                    pos += 2
            elif cmd == 0xCE:
                note = last_note
                if pos < rom_len and rom_data[pos] < 0x80:
                    note = rom_data[pos]
                    pos += 1
                eff_note = max(0, min(127, note + key_shift))
                if eff_note in active_ties:
                    st_tick, st_vel, st_voice = active_ties.pop(eff_note)
                    dur = max(1, tick - st_tick)
                    all_notes.append({
                        "track": t_idx, "tick": st_tick, "dur": dur,
                        "voice": st_voice, "key": eff_note, "vel": st_vel,
                        "pan": pan, "vol": vol,
                    })
                all_events.append({"track": t_idx, "tick": tick, "type": "note_off", "key": eff_note})
            elif cmd == 0xCF:
                note = last_note
                vel = last_vel
                if pos < rom_len and rom_data[pos] < 0x80:
                    note = rom_data[pos]
                    pos += 1
                    if pos < rom_len and rom_data[pos] < 0x80:
                        vel = rom_data[pos]
                        pos += 1
                last_note, last_vel = note, vel
                eff_note = max(0, min(127, note + key_shift))
                active_ties[eff_note] = (tick, vel, voice)
                all_events.append({"track": t_idx, "tick": tick, "type": "note_on", "key": eff_note, "vel": vel})
            elif cmd >= 0xD0:
                dur = M4A_WAIT_TABLE[cmd - 0xCF]
                note = last_note
                vel = last_vel
                if pos < rom_len and rom_data[pos] < 0x80:
                    note = rom_data[pos]
                    pos += 1
                    if pos < rom_len and rom_data[pos] < 0x80:
                        vel = rom_data[pos]
                        pos += 1
                        if pos < rom_len and rom_data[pos] < 0x80:
                            dur += rom_data[pos]
                            pos += 1
                last_note, last_vel = note, vel
                eff_note = max(0, min(127, note + key_shift))
                all_events.append({"track": t_idx, "tick": tick, "type": "note_on", "key": eff_note, "vel": vel})
                all_events.append({"track": t_idx, "tick": tick + dur, "type": "note_off", "key": eff_note})
                all_notes.append({
                    "track": t_idx, "tick": tick, "dur": dur,
                    "voice": voice, "key": eff_note, "vel": vel,
                    "pan": pan, "vol": vol,
                })

        for eff_note, (st_tick, st_vel, st_voice) in active_ties.items():
            dur = max(1, tick - st_tick)
            all_notes.append({
                "track": t_idx, "tick": st_tick, "dur": dur,
                "voice": st_voice, "key": eff_note, "vel": st_vel,
                "pan": pan, "vol": vol,
            })
            all_events.append({"track": t_idx, "tick": tick, "type": "note_off", "key": eff_note})

        max_tick = max(max_tick, tick)

    return {
        "events": all_events,
        "notes": all_notes,
        "tempos": sorted(tempos, key=lambda x: x[0]),
        "total_ticks": max(24, max_tick),
    }


# =============================================================================
# UNIVERSAL ROM SAMPLE & SOUND EFFECT SCANNER (FILTERED AGAINST GRAPHICS/TILES)
# =============================================================================
def _is_valid_pcm8_waveform(pcm_i8: np.ndarray) -> bool:
    """Verifies true audio waveform continuity (rejects 4bpp tiles, tilemaps & ARM code)."""
    if len(pcm_i8) < 1024:
        return False
    # Reject blocks dominated by 0x00 / 0xFF padding
    if float(np.mean((pcm_i8 == 0) | (pcm_i8 == -1))) > 0.25:
        return False

    x = pcm_i8.astype(np.float32)
    x0, x1 = x[:-1], x[1:]
    denom = float(np.sqrt(np.sum(x0 * x0) * np.sum(x1 * x1))) + 1e-6
    lag1_corr = float(np.sum(x0 * x1)) / denom
    # Real sampled audio waveforms exhibit strong adjacent-sample correlation
    if lag1_corr < 0.45:
        return False

    # Reject periodic 32-byte tile rows where lag-32 correlation > lag-1 correlation
    if len(x) > 128:
        x32_a, x32_b = x[:-32], x[32:]
        d32 = float(np.sqrt(np.sum(x32_a * x32_a) * np.sum(x32_b * x32_b))) + 1e-6
        lag32_corr = float(np.sum(x32_a * x32_b)) / d32
        if lag32_corr > lag1_corr + 0.10:
            return False

    return True


def scan_universal_rom_samples(rom_data: bytes) -> list:
    """
    Scans ANY GBA ROM for audio assets:
      1. M4A DirectSound WaveData headers
      2. Autocorrelation-verified 8-bit signed PCM waveforms & SFX clips
    """
    rom_len = len(rom_data)
    if rom_len < 0x400:
        return []

    found = []
    covered_ranges = []

    def _is_covered(off: int) -> bool:
        return any(s <= off < e for s, e in covered_ranges)

    # 1. M4A WaveData headers
    for off in range(0xC0, rom_len - 32, 4):
        if _is_covered(off):
            continue
        if rom_data[off] in (0x00, 0x01) and rom_data[off + 1] == 0x00 and rom_data[off + 2] == 0x00 and rom_data[off + 3] in (0x00, 0x40):
            smp = _read_sound_sample(rom_data, off)
            if smp and smp["loop_end"] >= 256 and int(np.ptp(smp["pcm_i8"])) >= 24:
                end_off = off + 16 + smp["loop_end"]
                covered_ranges.append((off, end_off))
                found.append({
                    "kind": "M4A WaveData",
                    "offset": off,
                    "length": smp["loop_end"],
                    "sample_rate": int(round(smp["sample_rate"])),
                    "loop": smp["loop"],
                    "signed": True,
                    "pcm": smp["pcm_i8"],
                })

    # 2. Autocorrelation-verified Raw 8-bit PCM scanner
    raw_i8 = np.frombuffer(rom_data, dtype=np.int8)
    win = 256
    n_wins = rom_len // win
    if n_wins > 4:
        blocks = raw_i8[:n_wins * win].reshape(n_wins, win).astype(np.int16)
        diffs = np.abs(np.diff(blocks, axis=1)).mean(axis=1)
        ptps = blocks.max(axis=1) - blocks.min(axis=1)
        means = np.abs(blocks.mean(axis=1))
        is_audio = (ptps >= 48) & (diffs >= 2.0) & (diffs <= 28.0) & (means <= 22.0)

        idx = 0
        while idx < n_wins:
            if not is_audio[idx]:
                idx += 1
                continue
            j = idx
            while j < n_wins and is_audio[j]:
                j += 1
            if (j - idx) >= 4:  # >= 1024 bytes
                byte_start = idx * win
                byte_end = min(rom_len, j * win)
                if not _is_covered(byte_start):
                    pcm_slice = raw_i8[byte_start:byte_end]
                    if _is_valid_pcm8_waveform(pcm_slice):
                        covered_ranges.append((byte_start, byte_end))
                        found.append({
                            "kind": "Raw PCM8",
                            "offset": byte_start,
                            "length": len(pcm_slice),
                            "sample_rate": 13379,
                            "loop": False,
                            "signed": True,
                            "pcm": pcm_slice,
                        })
                        if len(found) >= 800:
                            break
            idx = j

    found.sort(key=lambda item: item["offset"])
    return found


# =============================================================================
# MIDI (.mid) EXPORTER
# =============================================================================
def _write_vlq(val: int) -> bytes:
    buf = [val & 0x7F]
    val >>= 7
    while val > 0:
        buf.append((val & 0x7F) | 0x80)
        val >>= 7
    buf.reverse()
    return bytes(buf)


def export_song_to_midi(parsed_song: dict, track_count: int, out_path: str):
    ppqn = 24
    track_chunks = []

    for t_idx in range(max(1, track_count)):
        ch = t_idx if t_idx < 9 else (t_idx + 1) % 16
        t_evs = [ev for ev in parsed_song["events"] if ev["track"] == t_idx]
        t_evs.sort(key=lambda e: e["tick"])

        trk_bytes = bytearray()
        prev_tick = 0
        for ev in t_evs:
            delta = max(0, ev["tick"] - prev_tick)
            prev_tick = ev["tick"]
            et = ev["type"]

            if et == "tempo":
                us_per_quarter = max(100000, int(60_000_000 / max(1, ev["bpm"])))
                trk_bytes.extend(_write_vlq(delta))
                trk_bytes.extend(b"\xFF\x51\x03" + us_per_quarter.to_bytes(3, "big"))
            elif et == "program":
                trk_bytes.extend(_write_vlq(delta))
                trk_bytes.extend(bytes([0xC0 | ch, ev["val"] & 0x7F]))
            elif et == "cc":
                trk_bytes.extend(_write_vlq(delta))
                trk_bytes.extend(bytes([0xB0 | ch, ev["cc"] & 0x7F, ev["val"] & 0x7F]))
            elif et == "bend":
                val14 = max(0, min(16383, 8192 + (ev["val"] * 64)))
                trk_bytes.extend(_write_vlq(delta))
                trk_bytes.extend(bytes([0xE0 | ch, val14 & 0x7F, (val14 >> 7) & 0x7F]))
            elif et == "note_on":
                trk_bytes.extend(_write_vlq(delta))
                trk_bytes.extend(bytes([0x90 | ch, ev["key"] & 0x7F, ev["vel"] & 0x7F]))
            elif et == "note_off":
                trk_bytes.extend(_write_vlq(delta))
                trk_bytes.extend(bytes([0x80 | ch, ev["key"] & 0x7F, 0]))

        trk_bytes.extend(b"\x00\xFF\x2F\x00")
        track_chunks.append(b"MTrk" + struct.pack(">I", len(trk_bytes)) + bytes(trk_bytes))

    mthd = b"MThd" + struct.pack(">IHHH", 6, 1, len(track_chunks), ppqn)
    with open(out_path, "wb") as f:
        f.write(mthd)
        for chunk in track_chunks:
            f.write(chunk)


# =============================================================================
# SF2 SOUNDFONT 2.0 (.sf2) EXPORTER
# =============================================================================
def _riff_chunk(tag: bytes, payload: bytes) -> bytes:
    if len(payload) % 2 == 1:
        payload += b"\x00"
    return tag + struct.pack("<I", len(payload)) + payload


def _riff_list(list_type: bytes, body: bytes) -> bytes:
    return b"LIST" + struct.pack("<I", 4 + len(body)) + list_type + body


def export_voicegroup_to_sf2(voicegroup: list, out_path: str, bank_name: str = "GBA M4A Soundbank"):
    """Writes a spec-compliant RIFF SoundFont 2.0 (.sf2) from parsed M4A PCM samples."""
    pcm_insts = [v for v in voicegroup if v.get("kind") in ("pcm", "keysplit") and v.get("sample")]
    if not pcm_insts:
        raise ValueError("Selected song's voicegroup contains no DirectSound PCM samples.")

    smpl_buf = bytearray()
    shdr_buf = bytearray()
    phdr_buf = bytearray()
    pbag_buf = bytearray()
    pgen_buf = bytearray()
    inst_buf = bytearray()
    ibag_buf = bytearray()
    igen_buf = bytearray()

    for idx, v in enumerate(pcm_insts):
        smp = v["sample"]
        pcm_i16 = smp["pcm_i8"].astype(np.int16) * 256
        start_sample = len(smpl_buf) // 2
        smpl_buf.extend(pcm_i16.tobytes())
        end_sample = len(smpl_buf) // 2

        loop_start = start_sample + (smp["loop_start"] if smp["loop"] else 0)
        loop_end = start_sample + smp["loop_end"] if smp["loop"] else end_sample
        smpl_buf.extend(b"\x00" * (46 * 2))

        smp_name = f"GBA_SMP_{v['program']:03d}".encode("ascii")[:20].ljust(20, b"\x00")
        shdr_buf.extend(struct.pack(
            "<20sIIIIIBbHH",
            smp_name,
            start_sample,
            end_sample,
            loop_start,
            loop_end,
            int(round(smp["sample_rate"])),
            v["base_key"] & 0x7F,
            0,
            0,
            1,
        ))

        inst_name = f"Voice_{v['program']:03d}".encode("ascii")[:20].ljust(20, b"\x00")
        inst_buf.extend(struct.pack("<20sH", inst_name, idx))
        ibag_buf.extend(struct.pack("<HH", idx * 2, 0))
        igen_buf.extend(struct.pack("<HH", 54, 1 if smp["loop"] else 0))
        igen_buf.extend(struct.pack("<HH", 53, idx))

        preset_name = f"P_{v['program']:03d}".encode("ascii")[:20].ljust(20, b"\x00")
        phdr_buf.extend(struct.pack("<20sHHHIII", preset_name, v["program"] & 0x7F, 0, idx, 0, 0, 0))
        pbag_buf.extend(struct.pack("<HH", idx, 0))
        pgen_buf.extend(struct.pack("<HH", 41, idx))

    n = len(pcm_insts)
    shdr_buf.extend(struct.pack("<20sIIIIIBbHH", b"EOS".ljust(20, b"\x00"), 0, 0, 0, 0, 0, 0, 0, 0, 0))
    inst_buf.extend(struct.pack("<20sH", b"EOI".ljust(20, b"\x00"), n))
    ibag_buf.extend(struct.pack("<HH", n * 2, 0))
    igen_buf.extend(struct.pack("<HH", 0, 0))
    phdr_buf.extend(struct.pack("<20sHHHIII", b"EOP".ljust(20, b"\x00"), 255, 255, n, 0, 0, 0))
    pbag_buf.extend(struct.pack("<HH", n, 0))
    pgen_buf.extend(struct.pack("<HH", 0, 0))

    pmod_buf = struct.pack("<HHhHH", 0, 0, 0, 0, 0)
    imod_buf = struct.pack("<HHhHH", 0, 0, 0, 0, 0)

    info_body = (
        _riff_chunk(b"ifil", struct.pack("<HH", 2, 1))
        + _riff_chunk(b"isng", b"EMU8000\x00")
        + _riff_chunk(b"INAM", bank_name.encode("ascii", errors="ignore") + b"\x00")
    )
    sdta_body = _riff_chunk(b"smpl", bytes(smpl_buf))
    pdta_body = (
        _riff_chunk(b"phdr", bytes(phdr_buf))
        + _riff_chunk(b"pbag", bytes(pbag_buf))
        + _riff_chunk(b"pmod", pmod_buf)
        + _riff_chunk(b"pgen", bytes(pgen_buf))
        + _riff_chunk(b"inst", bytes(inst_buf))
        + _riff_chunk(b"ibag", bytes(ibag_buf))
        + _riff_chunk(b"imod", imod_buf)
        + _riff_chunk(b"igen", bytes(igen_buf))
        + _riff_chunk(b"shdr", bytes(shdr_buf))
    )

    sfbk_body = _riff_list(b"INFO", info_body) + _riff_list(b"sdta", sdta_body) + _riff_list(b"pdta", pdta_body)
    with open(out_path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + len(sfbk_body)) + b"sfbk" + sfbk_body)


# =============================================================================
# M4A POLYPHONIC WAVE SYNTHESIZER & WAV EXPORT
# =============================================================================
def _tick_to_seconds_fn(tempos: list):
    segments = []
    cur_t = 0
    cur_s = 0.0
    cur_bpm = 120
    for tk, bpm in tempos:
        if tk > cur_t:
            cur_s += ((tk - cur_t) / 24.0) * (60.0 / cur_bpm)
            segments.append((cur_t, cur_s, cur_bpm))
            cur_t = tk
        cur_bpm = max(20, bpm)
    segments.append((cur_t, cur_s, cur_bpm))

    def convert(target_tick: int) -> float:
        base_t, base_s, bpm = segments[0]
        for st_t, st_s, st_bpm in segments:
            if target_tick >= st_t:
                base_t, base_s, bpm = st_t, st_s, st_bpm
            else:
                break
        return base_s + ((target_tick - base_t) / 24.0) * (60.0 / bpm)

    return convert


def render_song_to_pcm(parsed_song: dict, voicegroup: list, sample_rate: int = 44100, max_seconds: float = 120.0) -> np.ndarray:
    """Renders decoded M4A notes & DirectSound/PSG samples into stereo int16 PCM."""
    tick_to_sec = _tick_to_seconds_fn(parsed_song["tempos"])
    dur_sec = min(max_seconds, tick_to_sec(parsed_song["total_ticks"]) + 1.0)
    total_samples = max(sample_rate, int(dur_sec * sample_rate))

    mix = np.zeros((total_samples, 2), dtype=np.float32)
    vg_map = {v["program"]: v for v in voicegroup}

    for n in parsed_song["notes"]:
        t0 = tick_to_sec(n["tick"])
        t1 = tick_to_sec(n["tick"] + n["dur"])
        if t0 >= dur_sec:
            continue

        s0 = max(0, int(t0 * sample_rate))
        s1 = min(total_samples, int((t1 + 0.08) * sample_rate))
        n_len = s1 - s0
        if n_len <= 16:
            continue

        inst = vg_map.get(n["voice"])
        play_key = n["key"]

        if inst and inst.get("kind") == "drumkit":
            subs = {s["program"]: s for s in inst.get("sub_entries", [])}
            inst = subs.get(n["key"], next(iter(subs.values()), None))
            if inst:
                play_key = inst.get("drum_key", 60)
        elif inst and inst.get("kind") == "keysplit":
            kt = inst.get("key_table", b"")
            subs = {s["program"]: s for s in inst.get("sub_entries", [])}
            if kt and 0 <= n["key"] < len(kt):
                sub_idx = kt[n["key"]]
                inst = subs.get(sub_idx, inst)

        gain = (n["vel"] / 127.0) * (n["vol"] / 127.0) * 0.28
        pan_norm = n["pan"] / 127.0
        l_gain = gain * np.cos(pan_norm * (np.pi / 2.0))
        r_gain = gain * np.sin(pan_norm * (np.pi / 2.0))

        t_arr = np.arange(n_len, dtype=np.float32)
        if inst and inst.get("kind") == "pcm" and inst.get("sample"):
            smp = inst["sample"]
            pcm = smp["pcm_i8"].astype(np.float32) / 128.0
            if inst.get("fixed_freq"):
                pitch_mult = 1.0
            else:
                pitch_mult = 2.0 ** ((play_key - inst.get("base_key", 60)) / 12.0)
            step = (smp["sample_rate"] / sample_rate) * pitch_mult
            pos = t_arr * step

            if smp["loop"] and smp["loop_end"] > smp["loop_start"]:
                l_start = float(smp["loop_start"])
                l_span = float(smp["loop_end"] - smp["loop_start"])
                over = pos >= smp["loop_end"]
                pos[over] = l_start + np.mod(pos[over] - l_start, l_span)
                valid = np.ones(n_len, dtype=bool)
            else:
                valid = pos < (len(pcm) - 1)

            idx0 = np.clip(pos.astype(np.int32), 0, len(pcm) - 1)
            wave_mono = np.where(valid, pcm[idx0], 0.0)
        elif inst and inst.get("kind") == "noise":
            rng = np.random.default_rng(seed=play_key)
            wave_mono = rng.uniform(-0.5, 0.5, size=n_len).astype(np.float32)
        else:
            freq = 440.0 * (2.0 ** ((play_key - 69) / 12.0))
            phase = (t_arr * freq / sample_rate) % 1.0
            wave_mono = np.where(phase < 0.5, 0.55, -0.55).astype(np.float32)

        rel = min(int(0.06 * sample_rate), n_len)
        if rel > 0:
            wave_mono[-rel:] *= np.linspace(1.0, 0.0, rel, dtype=np.float32)

        mix[s0:s1, 0] += wave_mono * l_gain
        mix[s0:s1, 1] += wave_mono * r_gain

    peak = float(np.max(np.abs(mix)))
    if peak > 0.95:
        mix *= (0.92 / peak)

    return np.clip(mix * 32767.0, -32768.0, 32767.0).astype(np.int16)


def export_song_to_wav(pcm_stereo: np.ndarray, out_path: str, sample_rate: int = 44100):
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_stereo.tobytes())


class _SappyScanThread(QThread):
    finished_scan = pyqtSignal(list, list)

    def __init__(self, rom_bytes: bytes):
        super().__init__()
        self.rom_bytes = rom_bytes

    def run(self):
        tables = scan_sappy_song_tables(self.rom_bytes)
        samples = scan_universal_rom_samples(self.rom_bytes)
        self.finished_scan.emit(tables, samples)


class SappyExtractorDialog(QDialog):
    """Universal GBA Music, Soundbank & Live Hardware Audio Extractor."""

    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("GBA Music, Soundbank & Universal Audio Extractor")
        self.resize(840, 620)

        self.rom_data = b""
        self.tables = []
        self.active_songs = []
        self.universal_samples = []
        self._stop_play_evt = threading.Event()
        self._play_thread = None

        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(100)
        self._rec_timer.timeout.connect(self._update_live_rec_status)

        self._init_ui()
        self.scan_rom()

    def _init_ui(self):
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()

        # TAB 1: M4A / SAPPY SONG TABLE EXTRACTOR
        tab_sappy = QWidget()
        s_lay = QVBoxLayout(tab_sappy)

        top = QHBoxLayout()
        top.addWidget(QLabel("Detected Song Table:"))
        self.table_combo = QComboBox()
        self.table_combo.currentIndexChanged.connect(self.on_table_changed)
        top.addWidget(self.table_combo, 2)

        top.addWidget(QLabel("Manual Offset:"))
        self.manual_off_edit = QLineEdit()
        self.manual_off_edit.setPlaceholderText("0x000000")
        self.manual_off_edit.setFixedWidth(95)
        top.addWidget(self.manual_off_edit)

        btn_load_off = QPushButton("Load Offset")
        btn_load_off.clicked.connect(self.load_manual_offset)
        top.addWidget(btn_load_off)

        btn_rescan = QPushButton("🔍 Rescan ROM")
        btn_rescan.clicked.connect(self.scan_rom)
        top.addWidget(btn_rescan)
        s_lay.addLayout(top)

        self.status_lbl = QLabel("Scanning ROM...")
        self.status_lbl.setStyleSheet("color: #90CAF9; font-weight: bold;")
        s_lay.addWidget(self.status_lbl)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Song ID", "Header Offset", "Tracks", "Player Grp", "Voicegroup Ptr", "Samples"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        s_lay.addWidget(self.table)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Loops:"))
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 5)
        self.loop_spin.setValue(1)
        ctrl.addWidget(self.loop_spin)

        self.btn_play = QPushButton("▶ Play")
        self.btn_play.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 10px;")
        self.btn_play.clicked.connect(self.play_selected)
        ctrl.addWidget(self.btn_play)

        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.clicked.connect(self.stop_playback)
        ctrl.addWidget(self.btn_stop)

        ctrl.addStretch()

        self.btn_midi = QPushButton("Export .MID...")
        self.btn_midi.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 6px;")
        self.btn_midi.clicked.connect(self.export_midi)
        ctrl.addWidget(self.btn_midi)

        self.btn_wav = QPushButton("Export .WAV...")
        self.btn_wav.setStyleSheet("background-color: #00897B; color: white; font-weight: bold; padding: 6px;")
        self.btn_wav.clicked.connect(self.export_wav)
        ctrl.addWidget(self.btn_wav)

        self.btn_sf2 = QPushButton("Export .SF2...")
        self.btn_sf2.setStyleSheet("background-color: #6A1B9A; color: white; font-weight: bold; padding: 6px;")
        self.btn_sf2.clicked.connect(self.export_sf2)
        ctrl.addWidget(self.btn_sf2)

        self.btn_batch = QPushButton("📦 Export All (.WAV/.MID)")
        self.btn_batch.setStyleSheet("background-color: #E65100; color: white; font-weight: bold; padding: 6px;")
        self.btn_batch.clicked.connect(self.batch_export_songs)
        ctrl.addWidget(self.btn_batch)
        s_lay.addLayout(ctrl)

        self.tabs.addTab(tab_sappy, "🎼 M4A / Sappy Sequences")

        # TAB 2: UNIVERSAL ROM SOUND & SAMPLE RIPPER
        tab_samples = QWidget()
        u_lay = QVBoxLayout(tab_samples)

        u_info = QLabel(
            "Scans the entire ROM for M4A WaveData headers and verified 8-bit PCM SFX/voice clips "
            "(works even on games with non-standard or proprietary music engines)."
        )
        u_info.setWordWrap(True)
        u_info.setStyleSheet("color: #B0BEC5; padding: 4px;")
        u_lay.addWidget(u_info)

        self.sample_table = QTableWidget(0, 5)
        self.sample_table.setHorizontalHeaderLabels([
            "Index", "Format", "ROM Offset", "Length (Samples)", "Rate (Hz)"
        ])
        self.sample_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.sample_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        u_lay.addWidget(self.sample_table)

        u_ctrl = QHBoxLayout()
        u_ctrl.addWidget(QLabel("Override Rate (Hz):"))
        self.smp_rate_spin = QSpinBox()
        self.smp_rate_spin.setRange(4000, 48000)
        self.smp_rate_spin.setValue(0)
        self.smp_rate_spin.setSpecialValueText("Auto")
        u_ctrl.addWidget(self.smp_rate_spin)

        self.cb_unsigned = QCheckBox("Unsigned 8-bit PCM")
        u_ctrl.addWidget(self.cb_unsigned)

        btn_play_smp = QPushButton("▶ Play Sample")
        btn_play_smp.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 6px 10px;")
        btn_play_smp.clicked.connect(self.play_selected_sample)
        u_ctrl.addWidget(btn_play_smp)

        btn_stop_smp = QPushButton("⏹ Stop")
        btn_stop_smp.clicked.connect(self.stop_playback)
        u_ctrl.addWidget(btn_stop_smp)

        u_ctrl.addStretch()

        btn_exp_smp = QPushButton("Export Sample .WAV...")
        btn_exp_smp.setStyleSheet("background-color: #00897B; color: white; font-weight: bold; padding: 6px;")
        btn_exp_smp.clicked.connect(self.export_selected_sample)
        u_ctrl.addWidget(btn_exp_smp)

        btn_exp_all_smp = QPushButton("📦 Export All Samples...")
        btn_exp_all_smp.setStyleSheet("background-color: #1976D2; color: white; font-weight: bold; padding: 6px;")
        btn_exp_all_smp.clicked.connect(self.batch_export_samples)
        u_ctrl.addWidget(btn_exp_all_smp)
        u_lay.addLayout(u_ctrl)

        self.tabs.addTab(tab_samples, "🔊 Universal ROM Samples / SFX")

        # TAB 3: LIVE HARDWARE AUDIO RECORDER
        tab_live = QWidget()
        l_lay = QVBoxLayout(tab_live)

        l_desc = QLabel(
            "<b>Universal Live GBA Audio Capture (Works on 100% of GBA Games &amp; Drivers)</b><br><br>"
            "Captures raw, lossless stereo PCM directly from mGBA's emulated GBA audio hardware "
            "(PSG channels 1–4 + DirectSound DMA A/B), bypassing mute/volume settings.<br>"
            "<i>Tip: Open the game's Sound Test menu (or play the level whose track you want), "
            "click Start Capture, then Save .WAV!</i>"
        )
        l_desc.setWordWrap(True)
        l_desc.setStyleSheet("background: #161A22; color: #CFD8DC; padding: 12px; border-radius: 6px;")
        l_lay.addWidget(l_desc)

        self.cb_trim_silence = QCheckBox("Auto-trim leading & trailing silence on export")
        self.cb_trim_silence.setChecked(True)
        l_lay.addWidget(self.cb_trim_silence)

        self.live_status_lbl = QLabel("Status: Idle (0.0s captured)")
        self.live_status_lbl.setStyleSheet(
            "font-family: monospace; font-size: 14px; font-weight: bold; "
            "color: #80CBC4; background: #10131A; padding: 10px; border-radius: 6px;"
        )
        l_lay.addWidget(self.live_status_lbl)

        l_btns = QHBoxLayout()
        self.btn_live_start = QPushButton("🔴 Start Live Audio Capture")
        self.btn_live_start.setStyleSheet("background-color: #C62828; color: white; font-weight: bold; padding: 10px;")
        self.btn_live_start.clicked.connect(self.start_live_capture)
        l_btns.addWidget(self.btn_live_start)

        self.btn_live_stop = QPushButton("⏹ Stop & Save .WAV...")
        self.btn_live_stop.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; padding: 10px;")
        self.btn_live_stop.setEnabled(False)
        self.btn_live_stop.clicked.connect(self.stop_and_save_live_capture)
        l_btns.addWidget(self.btn_live_stop)
        l_lay.addLayout(l_btns)

        l_lay.addStretch()
        self.tabs.addTab(tab_live, "🎙️ Live Hardware Recorder (Any Game)")

        root.addWidget(self.tabs)

    def scan_rom(self):
        if not self.worker or not self.worker.rom_path or not os.path.exists(self.worker.rom_path):
            self.status_lbl.setText("No ROM file loaded.")
            return
        with open(self.worker.rom_path, "rb") as f:
            self.rom_data = f.read()

        self.status_lbl.setText("Scanning ROM for M4A/Sappy tables and raw audio samples...")
        self._scanner = _SappyScanThread(self.rom_data)
        self._scanner.finished_scan.connect(self._on_scan_finished)
        self._scanner.start()

    def _on_scan_finished(self, tables: list, samples: list):
        self.tables = tables
        self.universal_samples = samples

        self.table_combo.blockSignals(True)
        self.table_combo.clear()
        for t in tables:
            self.table_combo.addItem(
                f"0x{t['table_offset']:06X} ({len(t['songs'])} songs)", t
            )
        self.table_combo.blockSignals(False)

        if tables:
            self.status_lbl.setText(
                f"Found {len(tables)} M4A/Sappy table(s) and {len(samples)} raw audio sample(s)."
            )
            self.on_table_changed(0)
        else:
            self.status_lbl.setText(
                f"No standard M4A song table found (custom driver). Found {len(samples)} ROM sample(s) — "
                f"use Tab 2 (Samples) or Tab 3 (Live Hardware Recorder)!"
            )
            self.table.setRowCount(0)

        self._populate_sample_table(samples)

    def _populate_sample_table(self, samples: list):
        self.sample_table.setRowCount(len(samples))
        for idx, s in enumerate(samples):
            self.sample_table.setItem(idx, 0, QTableWidgetItem(f"{idx:03d}"))
            self.sample_table.setItem(idx, 1, QTableWidgetItem(s["kind"]))
            self.sample_table.setItem(idx, 2, QTableWidgetItem(f"0x{s['offset']:06X}"))
            self.sample_table.setItem(idx, 3, QTableWidgetItem(f"{s['length']:,}"))
            self.sample_table.setItem(idx, 4, QTableWidgetItem(str(s["sample_rate"])))

    def on_table_changed(self, idx: int):
        data = self.table_combo.itemData(idx)
        if not data:
            return
        self._populate_songs(data["songs"])

    def load_manual_offset(self):
        if not self.rom_data:
            return
        try:
            off = int(self.manual_off_edit.text().strip(), 16)
            if off >= 0x08000000:
                off -= 0x08000000
        except ValueError:
            QMessageBox.warning(self, "Invalid Offset", "Enter a valid hex offset (e.g. 0x454320).")
            return

        songs = _try_parse_song_table_at(self.rom_data, off)
        if not songs:
            QMessageBox.information(self, "Not Found", "No valid M4A song table entries at that offset.")
            return
        self._populate_songs(songs)

    def _populate_songs(self, songs: list):
        self.active_songs = songs
        self.table.setRowCount(len(songs))
        vg_sample_cache = {}
        for row, s in enumerate(songs):
            vg_ptr = s["voicegroup_ptr"]
            if vg_ptr not in vg_sample_cache:
                vg = read_voicegroup(self.rom_data, vg_ptr)
                vg_sample_cache[vg_ptr] = sum(
                    1 for v in vg if v.get("kind") in ("pcm", "keysplit") and v.get("sample")
                )

            self.table.setItem(row, 0, QTableWidgetItem(f"{s['song_id']:03d}"))
            self.table.setItem(row, 1, QTableWidgetItem(f"0x{s['header_offset']:06X}"))
            self.table.setItem(row, 2, QTableWidgetItem(str(s["track_count"])))
            self.table.setItem(row, 3, QTableWidgetItem(str(s["player_group"])))
            self.table.setItem(row, 4, QTableWidgetItem(f"0x{vg_ptr:08X}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{vg_sample_cache[vg_ptr]} PCM"))

    def _get_selected_song(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Notice", "Select a song from the table first.")
            return None
        return self.active_songs[rows[0].row()]

    def stop_playback(self):
        self._stop_play_evt.set()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=0.5)
        self._play_thread = None

    def _play_pcm_async(self, pcm_stereo: np.ndarray, sample_rate: int):
        self.stop_playback()
        self._stop_play_evt.clear()

        def _worker():
            try:
                import sounddevice as sd
                with sd.OutputStream(samplerate=sample_rate, channels=2, dtype="int16", latency="low") as stream:
                    chunk = 2048
                    for idx in range(0, len(pcm_stereo), chunk):
                        if self._stop_play_evt.is_set():
                            break
                        stream.write(pcm_stereo[idx:idx + chunk])
            except Exception as e:
                print(f"[Audio Preview Error] {e}")

        self._play_thread = threading.Thread(target=_worker, daemon=True)
        self._play_thread.start()

    def play_selected(self):
        song = self._get_selected_song()
        if not song:
            return
        parsed = parse_m4a_song(self.rom_data, song, max_loops=self.loop_spin.value())
        vg = read_voicegroup(self.rom_data, song["voicegroup_ptr"])
        pcm = render_song_to_pcm(parsed, vg, sample_rate=44100)
        self.status_lbl.setText(f"▶ Playing Song #{song['song_id']:03d} ({len(pcm)/44100:.1f}s)...")
        self._play_pcm_async(pcm, 44100)

    def export_midi(self):
        song = self._get_selected_song()
        if not song:
            return
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        default_path = os.path.join(export_dir, f"song_{song['song_id']:03d}.mid")
        path, _ = QFileDialog.getSaveFileName(self, "Export MIDI", default_path, "MIDI Files (*.mid)")
        if not path:
            return
        parsed = parse_m4a_song(self.rom_data, song, max_loops=self.loop_spin.value())
        export_song_to_midi(parsed, song["track_count"], path)
        QMessageBox.information(self, "Exported", f"Saved MIDI sequence to:\n{path}")

    def export_wav(self):
        song = self._get_selected_song()
        if not song:
            return
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        default_path = os.path.join(export_dir, f"song_{song['song_id']:03d}.wav")
        path, _ = QFileDialog.getSaveFileName(self, "Export WAV", default_path, "WAV Audio (*.wav)")
        if not path:
            return
        parsed = parse_m4a_song(self.rom_data, song, max_loops=self.loop_spin.value())
        vg = read_voicegroup(self.rom_data, song["voicegroup_ptr"])
        pcm = render_song_to_pcm(parsed, vg, sample_rate=44100)
        export_song_to_wav(pcm, path, sample_rate=44100)
        QMessageBox.information(self, "Exported", f"Rendered WAV audio to:\n{path}")

    def export_sf2(self):
        song = self._get_selected_song()
        if not song:
            return
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        default_path = os.path.join(export_dir, f"soundbank_0x{song['voicegroup_ptr']:08X}.sf2")
        path, _ = QFileDialog.getSaveFileName(self, "Export SoundFont 2.0", default_path, "SoundFont 2 (*.sf2)")
        if not path:
            return
        vg = read_voicegroup(self.rom_data, song["voicegroup_ptr"])
        try:
            export_voicegroup_to_sf2(vg, path, bank_name=f"GBA_0x{song['voicegroup_ptr']:08X}")
            QMessageBox.information(self, "Exported", f"Exported SoundFont 2.0 to:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "SF2 Export Error", str(e))

    def batch_export_songs(self):
        if not self.active_songs:
            return
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        out_dir = QFileDialog.getExistingDirectory(self, "Select Folder to Export All Songs", export_dir)
        if not out_dir:
            return

        loops = self.loop_spin.value()
        vg_cache = {}
        exported = 0
        for s in self.active_songs:
            parsed = parse_m4a_song(self.rom_data, s, max_loops=loops)
            if not parsed["notes"]:
                continue
            sid = s["song_id"]
            export_song_to_midi(parsed, s["track_count"], os.path.join(out_dir, f"song_{sid:03d}.mid"))
            vg_ptr = s["voicegroup_ptr"]
            if vg_ptr not in vg_cache:
                vg_cache[vg_ptr] = read_voicegroup(self.rom_data, vg_ptr)
            pcm = render_song_to_pcm(parsed, vg_cache[vg_ptr], sample_rate=44100)
            export_song_to_wav(pcm, os.path.join(out_dir, f"song_{sid:03d}.wav"), sample_rate=44100)
            exported += 1

        QMessageBox.information(self, "Batch Export Complete", f"Exported {exported} song(s) (.mid + .wav) to:\n{out_dir}")

    def _sample_to_stereo_pcm(self, smp: dict) -> tuple[np.ndarray, int]:
        raw = smp["pcm"]
        if self.cb_unsigned.isChecked():
            u8 = raw.view(np.uint8).astype(np.int16) - 128
            i16 = (u8 * 256).astype(np.int16)
        else:
            i16 = raw.astype(np.int16) * 256
        stereo = np.column_stack((i16, i16))
        sr = self.smp_rate_spin.value() or smp["sample_rate"]
        return stereo, max(4000, min(48000, int(sr)))

    def _get_selected_sample(self) -> dict | None:
        rows = self.sample_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Notice", "Select a sample from the list first.")
            return None
        return self.universal_samples[rows[0].row()]

    def play_selected_sample(self):
        smp = self._get_selected_sample()
        if not smp:
            return
        stereo, sr = self._sample_to_stereo_pcm(smp)
        self._play_pcm_async(stereo, sr)

    def export_selected_sample(self):
        smp = self._get_selected_sample()
        if not smp:
            return
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        default_path = os.path.join(export_dir, f"sample_0x{smp['offset']:06X}.wav")
        path, _ = QFileDialog.getSaveFileName(self, "Export Sample WAV", default_path, "WAV Audio (*.wav)")
        if not path:
            return
        stereo, sr = self._sample_to_stereo_pcm(smp)
        export_song_to_wav(stereo, path, sample_rate=sr)
        QMessageBox.information(self, "Exported", f"Saved WAV sample to:\n{path}")

    def batch_export_samples(self):
        if not self.universal_samples:
            return
        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        out_dir = QFileDialog.getExistingDirectory(self, "Select Folder to Export All Samples", export_dir)
        if not out_dir:
            return
        for idx, smp in enumerate(self.universal_samples):
            stereo, sr = self._sample_to_stereo_pcm(smp)
            out_path = os.path.join(out_dir, f"sfx_{idx:03d}_0x{smp['offset']:06X}.wav")
            export_song_to_wav(stereo, out_path, sample_rate=sr)
        QMessageBox.information(
            self, "Batch Export Complete",
            f"Exported {len(self.universal_samples)} WAV sample(s) to:\n{out_dir}"
        )

    def start_live_capture(self):
        if not self.worker:
            return
        self.worker.start_audio_capture()
        self.btn_live_start.setEnabled(False)
        self.btn_live_stop.setEnabled(True)
        self._rec_timer.start()
        self._update_live_rec_status()

    def _update_live_rec_status(self):
        if not self.worker:
            return
        active, secs = self.worker.get_audio_capture_stats()
        if active:
            self.live_status_lbl.setText(f"● CAPTURING RAW GBA AUDIO: {secs:.1f}s")
            self.live_status_lbl.setStyleSheet(
                "font-family: monospace; font-size: 14px; font-weight: bold; "
                "color: #FF5252; background: #1B1014; padding: 10px; border-radius: 6px;"
            )

    def stop_and_save_live_capture(self):
        self._rec_timer.stop()
        self.btn_live_start.setEnabled(True)
        self.btn_live_stop.setEnabled(False)
        self.live_status_lbl.setText("Status: Idle (0.0s captured)")
        self.live_status_lbl.setStyleSheet(
            "font-family: monospace; font-size: 14px; font-weight: bold; "
            "color: #80CBC4; background: #10131A; padding: 10px; border-radius: 6px;"
        )

        if not self.worker:
            return
        pcm, sr = self.worker.stop_audio_capture()
        if len(pcm) == 0:
            QMessageBox.information(self, "Empty Capture", "No audio frames were captured (make sure emulation is unpaused).")
            return

        if self.cb_trim_silence.isChecked():
            mag = np.max(np.abs(pcm), axis=1)
            nz = np.where(mag > 24)[0]
            if len(nz) > 0:
                pcm = pcm[nz[0]:nz[-1] + 1]

        export_dir = self.config.get("paths", "exports") or "exports"
        os.makedirs(export_dir, exist_ok=True)
        rom_base = os.path.splitext(os.path.basename(self.worker.rom_path or "gba"))[0]
        default_path = os.path.join(export_dir, f"{rom_base}_capture.wav")
        path, _ = QFileDialog.getSaveFileName(self, "Save Captured GBA Audio", default_path, "WAV Audio (*.wav)")
        if path:
            export_song_to_wav(pcm, path, sample_rate=sr)
            QMessageBox.information(
                self, "Saved",
                f"Saved {len(pcm) / sr:.2f}s of lossless GBA audio ({sr} Hz) to:\n{path}"
            )

    def closeEvent(self, event):
        self._rec_timer.stop()
        if self.worker:
            active, _ = self.worker.get_audio_capture_stats()
            if active:
                self.worker.stop_audio_capture()
        self.stop_playback()
        event.accept()
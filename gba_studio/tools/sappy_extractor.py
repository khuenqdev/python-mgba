# sappy_extractor.py
import os
import struct
import wave
import threading
import numpy as np

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QLineEdit, QSpinBox, QComboBox, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

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

    if not (1 <= track_count <= 16) or block_count > 16:
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
        "header_offset": hdr_off,
        "track_count": track_count,
        "block_count": block_count,
        "priority": priority,
        "reverb": reverb,
        "voicegroup_ptr": vg_ptr,
        "track_ptrs": track_ptrs,
    }


def scan_sappy_song_tables(rom_data: bytes) -> list:
    """
    Scans a GBA ROM for Nintendo M4A / Sappy `song_table` structures.
    Returns a list of dicts: {table_offset, songs: [...]}.
    """
    rom_len = len(rom_data)
    if rom_len < 0x200:
        return []

    words = np.frombuffer(rom_data[:rom_len - (rom_len % 4)], dtype="<u4")
    # Song table entries are 8 bytes: word[even] = header_ptr, word[odd] = player_group | (pad << 16)
    hdr_words = words[0::2]
    grp_words = words[1::2]
    valid_entry = (
        (hdr_words >= 0x08000100)
        & (hdr_words < 0x08000000 + rom_len)
        & ((hdr_words & 3) == 0)
        & ((grp_words & 0xFFFF) < 32)
    )

    tables = []
    n_entries = len(valid_entry)
    i = 0
    while i < n_entries:
        if not valid_entry[i]:
            i += 1
            continue

        j = i
        while j < n_entries and valid_entry[j]:
            j += 1

        run_len = j - i
        if run_len >= 8:
            unique_headers = set()
            songs = []
            valid_headers = 0
            for k in range(run_len):
                entry_idx = i + k
                hdr_ptr = int(hdr_words[entry_idx])
                grp_word = int(grp_words[entry_idx])
                hdr_off = _ptr_to_off(hdr_ptr)
                parsed_hdr = _validate_song_header(rom_data, hdr_off)
                if parsed_hdr is None:
                    break
                valid_headers += 1
                unique_headers.add(hdr_off)
                songs.append({
                    "song_id": k,
                    "entry_offset": entry_idx * 8,
                    "player_group": grp_word & 0xFF,
                    **parsed_hdr,
                })

            if valid_headers >= 8 and len(unique_headers) >= 4:
                tables.append({
                    "table_offset": i * 8,
                    "songs": songs,
                })
                i = i + valid_headers
                continue
        i = j

    # Sort largest / richest table first
    tables.sort(key=lambda t: len(t["songs"]), reverse=True)
    return tables


# =============================================================================
# M4A VOICEGROUP & SAMPLE DECODER
# =============================================================================
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
                entries.append({
                    "program": idx,
                    "kind": "pcm",
                    "base_key": b1 & 0x7F,
                    "pan": b3 & 0x7F,
                    "adsr": ptr2,
                    "sample": smp_info,
                })
                continue

        if inst_type in (0x01, 0x02, 0x09, 0x0A):  # PSG Square 1/2
            entries.append({
                "program": idx,
                "kind": "square",
                "duty": ptr1 & 0x3,
                "base_key": 60,
                "adsr": ptr2,
            })
        elif inst_type in (0x03, 0x0B):  # PSG Wave
            entries.append({
                "program": idx,
                "kind": "square",
                "duty": 2,
                "base_key": 60,
                "adsr": ptr2,
            })
        elif inst_type in (0x04, 0x0C):  # PSG Noise
            entries.append({
                "program": idx,
                "kind": "noise",
                "base_key": 60,
                "adsr": ptr2,
            })
        elif inst_type == 0x40 and depth == 0 and _is_rom_ptr(ptr1, rom_len):
            # Keysplit instrument: resolve first available PCM sub-entry as representative sample
            sub = read_voicegroup(rom_data, ptr1, max_entries=32, depth=1)
            first_pcm = next((s for s in sub if s["kind"] == "pcm"), None)
            if first_pcm:
                entries.append({**first_pcm, "program": idx})
        elif inst_type == 0x80 and depth == 0 and _is_rom_ptr(ptr1, rom_len):
            sub = read_voicegroup(rom_data, ptr1, max_entries=64, depth=1)
            entries.append({
                "program": idx,
                "kind": "drumkit",
                "sub_entries": sub,
            })

    return entries


def _read_sound_sample(rom_data: bytes, smp_off: int) -> dict | None:
    rom_len = len(rom_data)
    if smp_off < 0 or smp_off + 16 > rom_len:
        return None
    _, loop_flag, freq_q10, loop_start, num_samples = struct.unpack_from("<HHIII", rom_data, smp_off)
    if num_samples == 0 or num_samples > 2_000_000:
        return None
    if smp_off + 16 + num_samples > rom_len:
        num_samples = max(0, rom_len - (smp_off + 16))
        if num_samples == 0:
            return None

    sample_rate = max(4000.0, min(48000.0, freq_q10 / 1024.0)) if freq_q10 > 0 else 13379.0
    pcm_i8 = np.frombuffer(rom_data, dtype=np.int8, count=num_samples, offset=smp_off + 16)
    is_loop = bool(loop_flag & 0x4000) and (loop_start < num_samples)

    return {
        "offset": smp_off,
        "sample_rate": sample_rate,
        "loop": is_loop,
        "loop_start": int(loop_start) if is_loop else 0,
        "loop_end": int(num_samples),
        "pcm_i8": pcm_i8,
    }


# =============================================================================
# M4A SEQUENCE PARSER
# =============================================================================
def parse_m4a_song(rom_data: bytes, song: dict, max_loops: int = 1) -> dict:
    """
    Decodes an M4A song header & all its tracks into normalized events:
      - notes: [{track, tick, dur, voice, key, vel, pan, vol}]
      - events: [{track, tick, type, ...}] (for exact MIDI export)
      - tempos: [(tick, bpm)]
      - total_ticks: int
    """
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
        bend = 0
        last_cmd = 0
        last_note = 60
        last_vel = 100
        active_ties = {}  # note_key -> (start_tick, vel, voice)

        steps = 0
        while 0 <= pos < rom_len and steps < 40000:
            steps += 1
            b = rom_data[pos]
            pos += 1

            # Running status handling
            if b < 0x80:
                if last_cmd >= 0xCF:
                    cmd = last_cmd
                    pos -= 1
                else:
                    continue
            else:
                cmd = b
                if cmd >= 0xCF or (0x80 <= cmd <= 0xB0):
                    last_cmd = cmd

            if 0x80 <= cmd <= 0xB0:
                tick += M4A_WAIT_TABLE[cmd - 0x80]
                continue

            if cmd == 0xB1:  # FINE
                break
            elif cmd == 0xB2:  # GOTO <ptr>
                if pos + 4 > rom_len:
                    break
                target = struct.unpack_from("<I", rom_data, pos)[0]
                pos += 4
                goto_count += 1
                if goto_count > max_loops or not _is_rom_ptr(target, rom_len):
                    break
                pos = _ptr_to_off(target)
            elif cmd == 0xB3:  # PATT <ptr>
                if pos + 4 > rom_len:
                    break
                target = struct.unpack_from("<I", rom_data, pos)[0]
                pos += 4
                if _is_rom_ptr(target, rom_len) and len(ret_stack) < 8:
                    ret_stack.append(pos)
                    pos = _ptr_to_off(target)
            elif cmd == 0xB4:  # PEND
                if ret_stack:
                    pos = ret_stack.pop()
                else:
                    break
            elif cmd == 0xB5:  # REPT <count> <ptr>
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
            elif cmd == 0xB9:  # MEMACC
                pos += 3
            elif cmd == 0xBA:  # PRIO
                pos += 1
            elif cmd == 0xBB:  # TEMPO
                if pos < rom_len:
                    bpm = max(20, min(300, rom_data[pos] * 2))
                    pos += 1
                    tempos.append((tick, bpm))
                    all_events.append({"track": t_idx, "tick": tick, "type": "tempo", "bpm": bpm})
            elif cmd == 0xBC:  # KEYSH
                if pos < rom_len:
                    key_shift = struct.unpack_from("<b", rom_data, pos)[0]
                    pos += 1
            elif cmd == 0xBD:  # VOICE
                if pos < rom_len:
                    voice = rom_data[pos] & 0x7F
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "program", "val": voice})
            elif cmd == 0xBE:  # VOL
                if pos < rom_len:
                    vol = rom_data[pos] & 0x7F
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "cc", "cc": 7, "val": vol})
            elif cmd == 0xBF:  # PAN
                if pos < rom_len:
                    pan = rom_data[pos] & 0x7F
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "cc", "cc": 10, "val": pan})
            elif cmd == 0xC0:  # BEND
                if pos < rom_len:
                    bend = struct.unpack_from("<b", rom_data, pos)[0]
                    pos += 1
                    all_events.append({"track": t_idx, "tick": tick, "type": "bend", "val": bend})
            elif cmd in (0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC8):
                pos += 1
            elif cmd == 0xCD:  # XCMD
                pos += 2
            elif cmd == 0xCE:  # EOT [note]
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
            elif cmd == 0xCF:  # TIE [note [vel]]
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
            elif cmd >= 0xD0:  # N01..N96
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

        # Close any dangling tie
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

        trk_bytes.extend(b"\x00\xFF\x2F\x00")  # End of Track
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
    pcm_insts = [v for v in voicegroup if v.get("kind") == "pcm" and v.get("sample")]
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
        # SF2 spec requires >= 46 zero samples after each sample
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
            1,  # monoSample
        ))

        # Instrument & Bag
        inst_name = f"Voice_{v['program']:03d}".encode("ascii")[:20].ljust(20, b"\x00")
        inst_buf.extend(struct.pack("<20sH", inst_name, idx))
        ibag_buf.extend(struct.pack("<HH", idx * 2, 0))
        # sampleModes (54): 1 = loop continuously, 0 = no loop
        igen_buf.extend(struct.pack("<HH", 54, 1 if smp["loop"] else 0))
        # sampleID (53)
        igen_buf.extend(struct.pack("<HH", 53, idx))

        # Preset & Bag
        preset_name = f"P_{v['program']:03d}".encode("ascii")[:20].ljust(20, b"\x00")
        phdr_buf.extend(struct.pack("<20sHHHIII", preset_name, v["program"] & 0x7F, 0, idx, 0, 0, 0))
        pbag_buf.extend(struct.pack("<HH", idx, 0))
        # instrument (41)
        pgen_buf.extend(struct.pack("<HH", 41, idx))

    # Terminal sentinel records required by SF2 spec
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
# M4A POLYPHONIC WAVE SYNTHESIZER & LIVE PLAYER
# =============================================================================
def _tick_to_seconds_fn(tempos: list):
    # Precompute piecewise-linear tick -> seconds map (24 PPQN)
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
        if inst and inst.get("kind") == "drumkit":
            subs = {s["program"]: s for s in inst.get("sub_entries", [])}
            inst = subs.get(n["key"], next(iter(subs.values()), None))

        gain = (n["vel"] / 127.0) * (n["vol"] / 127.0) * 0.28
        pan_norm = n["pan"] / 127.0
        l_gain = gain * np.cos(pan_norm * (np.pi / 2.0))
        r_gain = gain * np.sin(pan_norm * (np.pi / 2.0))

        t_arr = np.arange(n_len, dtype=np.float32)
        if inst and inst.get("kind") == "pcm" and inst.get("sample"):
            smp = inst["sample"]
            pcm = smp["pcm_i8"].astype(np.float32) / 128.0
            pitch_mult = 2.0 ** ((n["key"] - inst["base_key"]) / 12.0)
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
            rng = np.random.default_rng(seed=n["key"])
            wave_mono = rng.uniform(-0.5, 0.5, size=n_len).astype(np.float32)
        else:
            # PSG Square fallback
            freq = 440.0 * (2.0 ** ((n["key"] - 69) / 12.0))
            phase = (t_arr * freq / sample_rate) % 1.0
            wave_mono = np.where(phase < 0.5, 0.55, -0.55).astype(np.float32)

        # Smooth release tail
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
    finished_tables = pyqtSignal(list)

    def __init__(self, rom_bytes: bytes):
        super().__init__()
        self.rom_bytes = rom_bytes

    def run(self):
        tables = scan_sappy_song_tables(self.rom_bytes)
        self.finished_tables.emit(tables)


class SappyExtractorDialog(QDialog):
    """Sappy / M4A Soundbank & Music Extractor (Play, MIDI, WAV, SF2)."""

    def __init__(self, worker, config, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.config = config
        self.setWindowTitle("Audio Soundbank & Sappy (M4A) Music Extractor")
        self.resize(780, 560)

        self.rom_data = b""
        self.tables = []
        self.active_songs = []
        self._stop_play_evt = threading.Event()
        self._play_thread = None

        self._init_ui()
        self.scan_rom()

    def _init_ui(self):
        root = QVBoxLayout(self)

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
        root.addLayout(top)

        self.status_lbl = QLabel("Scanning ROM for M4A / Sappy song tables...")
        self.status_lbl.setStyleSheet("color: #90CAF9; font-weight: bold;")
        root.addWidget(self.status_lbl)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Song ID", "Header Offset", "Tracks", "Player Grp", "Voicegroup Ptr", "Samples"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self.table)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Loops:"))
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 5)
        self.loop_spin.setValue(1)
        ctrl.addWidget(self.loop_spin)

        self.btn_play = QPushButton("▶ Play Sequence")
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
        root.addLayout(ctrl)

    def scan_rom(self):
        if not self.worker or not self.worker.rom_path or not os.path.exists(self.worker.rom_path):
            self.status_lbl.setText("No ROM file loaded.")
            return
        with open(self.worker.rom_path, "rb") as f:
            self.rom_data = f.read()

        self.status_lbl.setText("Scanning ROM for M4A / Sappy song tables...")
        self._scanner = _SappyScanThread(self.rom_data)
        self._scanner.finished_tables.connect(self._on_tables_found)
        self._scanner.start()

    def _on_tables_found(self, tables: list):
        self.tables = tables
        self.table_combo.blockSignals(True)
        self.table_combo.clear()
        for t in tables:
            self.table_combo.addItem(
                f"0x{t['table_offset']:06X} ({len(t['songs'])} entries)", t
            )
        self.table_combo.blockSignals(False)

        if tables:
            self.status_lbl.setText(f"Found {len(tables)} candidate Sappy/M4A song table(s).")
            self.on_table_changed(0)
        else:
            self.status_lbl.setText("No standard Sappy/M4A song table detected (game may use a custom audio driver).")
            self.table.setRowCount(0)

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

        songs = []
        rom_len = len(self.rom_data)
        for k in range(512):
            pos = off + (k * 8)
            if pos + 8 > rom_len:
                break
            hdr_ptr, grp_word = struct.unpack_from("<II", self.rom_data, pos)
            if not _is_rom_ptr(hdr_ptr, rom_len) or (grp_word & 0xFFFF) >= 32:
                break
            parsed_hdr = _validate_song_header(self.rom_data, _ptr_to_off(hdr_ptr))
            if not parsed_hdr:
                break
            songs.append({
                "song_id": k,
                "entry_offset": pos,
                "player_group": grp_word & 0xFF,
                **parsed_hdr,
            })

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
                vg_sample_cache[vg_ptr] = sum(1 for v in vg if v.get("kind") == "pcm")

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

    def play_selected(self):
        song = self._get_selected_song()
        if not song:
            return
        self.stop_playback()

        parsed = parse_m4a_song(self.rom_data, song, max_loops=self.loop_spin.value())
        vg = read_voicegroup(self.rom_data, song["voicegroup_ptr"])
        pcm = render_song_to_pcm(parsed, vg, sample_rate=44100)

        self._stop_play_evt.clear()
        self.status_lbl.setText(f"▶ Playing Song #{song['song_id']:03d} ({len(pcm)/44100:.1f}s)...")

        def _worker():
            try:
                import sounddevice as sd
                with sd.OutputStream(samplerate=44100, channels=2, dtype="int16", latency="low") as stream:
                    chunk = 2048
                    for idx in range(0, len(pcm), chunk):
                        if self._stop_play_evt.is_set():
                            break
                        stream.write(pcm[idx:idx + chunk])
            except Exception as e:
                print(f"[Sappy Player Error] {e}")

        self._play_thread = threading.Thread(target=_worker, daemon=True)
        self._play_thread.start()

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

    def closeEvent(self, event):
        self.stop_playback()
        event.accept()

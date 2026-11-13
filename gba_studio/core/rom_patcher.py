# rom_patcher.py
import os
import zlib
import struct
import hashlib
import tempfile
import zipfile

PATCH_EXTS = (".bps", ".ups", ".ips")


class PatchError(Exception):
    """Raised when a ROM patch is invalid, corrupted, or mismatches the source ROM."""


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    """Decodes variable-length integers used by BPS and UPS specifications."""
    result = 0
    shift = 1
    while pos < len(data):
        x = data[pos]
        pos += 1
        result += (x & 0x7F) * shift
        if x & 0x80:
            return result, pos
        shift <<= 7
        result += shift
    raise PatchError("Unexpected end of patch while decoding variable-length integer.")


def apply_ips_patch(source: bytes, patch: bytes) -> bytes:
    if len(patch) < 8 or patch[:5] != b"PATCH":
        raise PatchError("Invalid IPS header (expected 'PATCH').")

    out = bytearray(source)
    pos = 5
    patch_len = len(patch)

    while pos + 3 <= patch_len:
        if patch[pos:pos + 3] == b"EOF":
            pos += 3
            if pos + 3 == patch_len:
                trunc_size = int.from_bytes(patch[pos:pos + 3], "big")
                if trunc_size < len(out):
                    del out[trunc_size:]
            return bytes(out)

        if pos + 5 > patch_len:
            raise PatchError("Truncated IPS record header.")

        offset = int.from_bytes(patch[pos:pos + 3], "big")
        size = int.from_bytes(patch[pos + 3:pos + 5], "big")
        pos += 5

        if size == 0:
            if pos + 3 > patch_len:
                raise PatchError("Truncated IPS RLE record.")
            rle_len = int.from_bytes(patch[pos:pos + 2], "big")
            rle_byte = patch[pos + 2]
            pos += 3
            end = offset + rle_len
            if end > len(out):
                out.extend(b"\x00" * (end - len(out)))
            out[offset:end] = bytes([rle_byte]) * rle_len
        else:
            if pos + size > patch_len:
                raise PatchError("Truncated IPS data record.")
            end = offset + size
            if end > len(out):
                out.extend(b"\x00" * (end - len(out)))
            out[offset:end] = patch[pos:pos + size]
            pos += size

    raise PatchError("IPS patch is missing the 'EOF' terminator.")


def apply_bps_patch(source: bytes, patch: bytes) -> bytes:
    if len(patch) < 16 or patch[:4] != b"BPS1":
        raise PatchError("Invalid BPS header (expected 'BPS1').")

    patch_crc_expected = struct.unpack("<I", patch[-4:])[0]
    patch_crc_actual = zlib.crc32(patch[:-4]) & 0xFFFFFFFF
    if patch_crc_actual != patch_crc_expected:
        raise PatchError("BPS patch file checksum is corrupted.")

    source_crc_expected = struct.unpack("<I", patch[-12:-8])[0]
    target_crc_expected = struct.unpack("<I", patch[-8:-4])[0]

    pos = 4
    source_size, pos = _read_vlq(patch, pos)
    target_size, pos = _read_vlq(patch, pos)
    metadata_size, pos = _read_vlq(patch, pos)
    pos += metadata_size

    if len(source) != source_size:
        raise PatchError(
            f"BPS source size mismatch (expected {source_size:,} bytes, got {len(source):,} bytes)."
        )

    source_crc_actual = zlib.crc32(source) & 0xFFFFFFFF
    if source_crc_actual != source_crc_expected:
        raise PatchError(
            f"BPS source ROM CRC32 mismatch (expected {source_crc_expected:08X}, got {source_crc_actual:08X})."
        )

    out = bytearray(target_size)
    out_offset = 0
    src_rel_offset = 0
    tgt_rel_offset = 0
    end_pos = len(patch) - 12

    while pos < end_pos:
        command, pos = _read_vlq(patch, pos)
        mode = command & 3
        length = (command >> 2) + 1

        if out_offset + length > target_size:
            raise PatchError("BPS output exceeds declared target size.")

        if mode == 0:  # SourceRead
            if out_offset + length > len(source):
                raise PatchError("BPS SourceRead out of bounds.")
            out[out_offset:out_offset + length] = source[out_offset:out_offset + length]
            out_offset += length

        elif mode == 1:  # TargetRead
            if pos + length > end_pos:
                raise PatchError("BPS TargetRead out of bounds.")
            out[out_offset:out_offset + length] = patch[pos:pos + length]
            pos += length
            out_offset += length

        elif mode == 2:  # SourceCopy
            enc, pos = _read_vlq(patch, pos)
            delta = (-1 if (enc & 1) else 1) * (enc >> 1)
            src_rel_offset += delta
            if src_rel_offset < 0 or src_rel_offset + length > len(source):
                raise PatchError("BPS SourceCopy out of bounds.")
            out[out_offset:out_offset + length] = source[src_rel_offset:src_rel_offset + length]
            src_rel_offset += length
            out_offset += length

        elif mode == 3:  # TargetCopy
            enc, pos = _read_vlq(patch, pos)
            delta = (-1 if (enc & 1) else 1) * (enc >> 1)
            tgt_rel_offset += delta
            if tgt_rel_offset < 0 or tgt_rel_offset + length > target_size:
                raise PatchError("BPS TargetCopy out of bounds.")
            for _ in range(length):
                out[out_offset] = out[tgt_rel_offset]
                out_offset += 1
                tgt_rel_offset += 1

    target_bytes = bytes(out)
    target_crc_actual = zlib.crc32(target_bytes) & 0xFFFFFFFF
    if target_crc_actual != target_crc_expected:
        raise PatchError(
            f"BPS target ROM CRC32 mismatch (expected {target_crc_expected:08X}, got {target_crc_actual:08X})."
        )

    return target_bytes


def apply_ups_patch(source: bytes, patch: bytes) -> bytes:
    if len(patch) < 16 or patch[:4] != b"UPS1":
        raise PatchError("Invalid UPS header (expected 'UPS1').")

    patch_crc_expected = struct.unpack("<I", patch[-4:])[0]
    patch_crc_actual = zlib.crc32(patch[:-4]) & 0xFFFFFFFF
    if patch_crc_actual != patch_crc_expected:
        raise PatchError("UPS patch file checksum is corrupted.")

    source_crc_expected = struct.unpack("<I", patch[-12:-8])[0]
    target_crc_expected = struct.unpack("<I", patch[-8:-4])[0]

    pos = 4
    source_size, pos = _read_vlq(patch, pos)
    target_size, pos = _read_vlq(patch, pos)

    if len(source) != source_size:
        raise PatchError(
            f"UPS source size mismatch (expected {source_size:,} bytes, got {len(source):,} bytes)."
        )

    source_crc_actual = zlib.crc32(source) & 0xFFFFFFFF
    if source_crc_actual != source_crc_expected:
        raise PatchError(
            f"UPS source ROM CRC32 mismatch (expected {source_crc_expected:08X}, got {source_crc_actual:08X})."
        )

    out = bytearray(target_size)
    copy_len = min(len(source), target_size)
    out[:copy_len] = source[:copy_len]

    end_pos = len(patch) - 12
    offset = 0

    while pos < end_pos:
        skip, pos = _read_vlq(patch, pos)
        offset += skip
        while pos < end_pos:
            xor_byte = patch[pos]
            pos += 1
            if xor_byte == 0:
                offset += 1
                break
            src_byte = source[offset] if offset < len(source) else 0
            if offset < target_size:
                out[offset] = src_byte ^ xor_byte
            offset += 1

    target_bytes = bytes(out)
    target_crc_actual = zlib.crc32(target_bytes) & 0xFFFFFFFF
    if target_crc_actual != target_crc_expected:
        raise PatchError(
            f"UPS target ROM CRC32 mismatch (expected {target_crc_expected:08X}, got {target_crc_actual:08X})."
        )

    return target_bytes


def apply_patch_bytes(source: bytes, patch: bytes, ext: str = "") -> bytes:
    if patch.startswith(b"BPS1"):
        return apply_bps_patch(source, patch)
    if patch.startswith(b"UPS1"):
        return apply_ups_patch(source, patch)
    if patch.startswith(b"PATCH"):
        return apply_ips_patch(source, patch)

    ext = ext.lower()
    if ext == ".bps":
        return apply_bps_patch(source, patch)
    if ext == ".ups":
        return apply_ups_patch(source, patch)
    if ext == ".ips":
        return apply_ips_patch(source, patch)

    raise PatchError("Unsupported or unrecognized patch file format.")


def find_matching_patch(rom_path: str, source_path: str = None) -> str | None:
    """Looks for a matching .bps/.ups/.ips sidecar patch next to the ROM or ZIP archive."""
    search_targets = []
    for candidate_path in (rom_path, source_path):
        if candidate_path and os.path.exists(candidate_path):
            parent = os.path.dirname(os.path.abspath(candidate_path))
            stem = os.path.splitext(os.path.basename(candidate_path))[0]
            search_targets.append((parent, stem))

    for parent, stem in search_targets:
        try:
            entries = os.listdir(parent)
        except OSError:
            continue

        lower_map = {name.lower(): name for name in entries}
        for ext in PATCH_EXTS:
            probe = f"{stem}{ext}".lower()
            if probe in lower_map:
                full = os.path.join(parent, lower_map[probe])
                if os.path.isfile(full):
                    return full

    # Also check inside a .zip archive if source_path is a zip containing both ROM & patch
    if source_path and source_path.lower().endswith(".zip") and os.path.exists(source_path):
        try:
            rom_stem = os.path.splitext(os.path.basename(rom_path))[0].lower()
            with zipfile.ZipFile(source_path, "r") as zf:
                for ext in PATCH_EXTS:
                    for zi in zf.infolist():
                        if zi.is_dir() or zi.filename.startswith("__MACOSX/"):
                            continue
                        base = os.path.basename(zi.filename).lower()
                        if base == f"{rom_stem}{ext}":
                            zip_hash = hashlib.md5(os.path.abspath(source_path).encode("utf-8")).hexdigest()[:12]
                            cache_dir = os.path.join(tempfile.gettempdir(), "gba_studio_rom_cache", zip_hash)
                            os.makedirs(cache_dir, exist_ok=True)
                            out_patch = os.path.join(cache_dir, os.path.basename(zi.filename))
                            if not os.path.exists(out_patch) or os.path.getsize(out_patch) != zi.file_size:
                                with zf.open(zi, "r") as src, open(out_patch, "wb") as dst:
                                    dst.write(src.read())
                            return out_patch
        except Exception:
            pass

    return None


def prepare_patched_rom(clean_rom_path: str, patch_path: str) -> str:
    """Applies patch_path to clean_rom_path and returns the cached patched ROM path."""
    stat_r = os.stat(clean_rom_path)
    stat_p = os.stat(patch_path)
    key = f"{os.path.abspath(clean_rom_path)}|{stat_r.st_mtime_ns}|{os.path.abspath(patch_path)}|{stat_p.st_mtime_ns}"
    cache_hash = hashlib.md5(key.encode("utf-8")).hexdigest()[:14]

    cache_dir = os.path.join(tempfile.gettempdir(), "gba_studio_rom_cache", "patched", cache_hash)
    os.makedirs(cache_dir, exist_ok=True)

    rom_filename = os.path.basename(clean_rom_path)
    patched_rom_path = os.path.join(cache_dir, rom_filename)

    if os.path.exists(patched_rom_path) and os.path.getsize(patched_rom_path) > 0:
        return patched_rom_path

    with open(clean_rom_path, "rb") as rf:
        rom_bytes = rf.read()
    with open(patch_path, "rb") as pf:
        patch_bytes = pf.read()

    ext = os.path.splitext(patch_path)[1]
    patched_bytes = apply_patch_bytes(rom_bytes, patch_bytes, ext=ext)

    tmp_path = patched_rom_path + ".tmp"
    with open(tmp_path, "wb") as out_f:
        out_f.write(patched_bytes)
    os.replace(tmp_path, patched_rom_path)

    return patched_rom_path
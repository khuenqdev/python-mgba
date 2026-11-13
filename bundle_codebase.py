#!/usr/bin/env python3
"""
bundle_codebase.py
Condenses the emulator codebase into a single AI-friendly context document.
"""

import os
import argparse
from datetime import datetime
from pathlib import Path

# Directories to skip completely (large assets, caches, virtualenvs, third-party shaders)
DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", "ENV",
    "models", "screenshots", "exports", "replays", "states",
    "saves", "translations", "shaders", "roms", "cheats",
    "samples", "build", "dist", ".idea", ".vscode"
}

# Binary, asset, and large model extensions to ignore
DEFAULT_IGNORE_EXTENSIONS = {
    # ROMs & Saves
    ".gba", ".gb", ".gbc", ".sgb", ".sav", ".srm", ".state", ".ss1", ".ss2", ".ss3",
    ".ss4", ".ss5", ".ss6", ".ss7", ".ss8", ".ss9", ".zip", ".7z", ".rar",
    # AI Models & Weights
    ".gguf", ".onnx", ".pth", ".pt", ".bin", ".tar", ".gz",
    # Libretro Cores & Native Binaries
    ".dll", ".so", ".dylib", ".exe", ".pyd", ".pyc",
    # Images & Media
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".ico", ".wav", ".mp3", ".ogg"
}

# Files that should never be bundled (sensitive secrets, self, generated lists)
DEFAULT_IGNORE_FILES = {
    ".env", ".env.local", ".env.production",
    "bundle_codebase.py",
    "codebase_structure.txt",
    "codebase_files.txt",
}

# File extensions prioritized for inclusion
VALID_TEXT_EXTENSIONS = {
    ".py", ".json", ".txt", ".md", ".yml", ".yaml",
    ".ini", ".cfg", ".toml", ".sh", ".bat", ".ps1"
}

EXACT_TEXT_FILENAMES = {".gitignore", ".env.sample", "LICENSE"}


def is_binary_file(filepath: Path, sample_size: int = 8192) -> bool:
    """
    Detect binary files using raw bytes instead of the OS locale encoding,
    so UTF-8/Japanese/Vietnamese text in .py files is never falsely rejected on Windows.
    """
    try:
        with open(filepath, "rb") as check_file:
            chunk = check_file.read(sample_size)
        if not chunk:
            return False
        if b"\x00" in chunk:
            return True

        # Check for non-text ASCII control bytes (excluding \a, \b, \t, \n, \f, \r, ESC)
        control_chars = set(range(0, 32)) - {7, 8, 9, 10, 12, 13, 27}
        non_text = sum(1 for b in chunk if b in control_chars)
        return (non_text / len(chunk)) > 0.10
    except OSError:
        return True


def build_tree_structure(root_dir: Path, collected_files: list[Path]) -> str:
    """Builds a properly nested visual directory tree showing only bundled files."""
    tree: dict = {}
    for f in collected_files:
        parts = f.relative_to(root_dir).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None

    lines = [f"{root_dir.resolve().name}/"]

    def _render(node: dict, prefix: str = "") -> None:
        # Directories first, then files (case-insensitive)
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
        for idx, (name, child) in enumerate(items):
            is_last = idx == len(items) - 1
            connector = "└── " if is_last else "├── "
            if child is not None:
                lines.append(f"{prefix}{connector}{name}/")
                extension = "    " if is_last else "│   "
                _render(child, prefix + extension)
            else:
                lines.append(f"{prefix}{connector}{name}")

    _render(tree)
    return "\n".join(lines)


def bundle_project(root_dir: Path, output_file: Path, max_file_size_kb: int = 500):
    root_dir = root_dir.resolve()
    output_resolved = output_file.resolve()
    collected_files: list[Path] = []
    skipped_files: list[tuple[Path, str]] = []

    # 1. Walk directory and filter eligible text files
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = sorted(d for d in dirs if d not in DEFAULT_IGNORE_DIRS)

        for file_name in sorted(files):
            file_path = Path(root) / file_name

            # Skip self, output file, and secret/generated files
            if file_name in DEFAULT_IGNORE_FILES or file_path.resolve() == output_resolved:
                continue

            ext = file_path.suffix.lower()
            if ext in DEFAULT_IGNORE_EXTENSIONS:
                continue

            if ext not in VALID_TEXT_EXTENSIONS and file_name not in EXACT_TEXT_FILENAMES:
                continue

            # Skip oversized files
            try:
                size_bytes = file_path.stat().st_size
                if size_bytes > (max_file_size_kb * 1024):
                    skipped_files.append((file_path.relative_to(root_dir), f"exceeds {max_file_size_kb} KB ({size_bytes / 1024:.1f} KB)"))
                    continue
            except OSError as e:
                skipped_files.append((file_path.relative_to(root_dir), f"stat error: {e}"))
                continue

            # Safe binary check
            if is_binary_file(file_path):
                skipped_files.append((file_path.relative_to(root_dir), "detected as binary"))
                continue

            collected_files.append(file_path)

    collected_files.sort(key=lambda p: p.relative_to(root_dir).as_posix().lower())

    # 2. Write formatted AI Context document
    total_lines = 0

    with open(output_file, "w", encoding="utf-8") as out:
        out.write("=" * 80 + "\n")
        out.write("PROJECT SOURCE CODE CONTEXT BUNDLE\n")
        out.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write(f"Root Directory: {root_dir.name}\n")
        out.write(f"Total Bundled Files: {len(collected_files)}\n")
        out.write("=" * 80 + "\n\n")

        # Visual Project Architecture Tree
        out.write("### 1. PROJECT STRUCTURE:\n")
        out.write("```text\n")
        out.write(build_tree_structure(root_dir, collected_files) + "\n")
        out.write("```\n\n")

        # Table of Contents
        out.write("### 2. TABLE OF CONTENTS:\n")
        for idx, f in enumerate(collected_files, start=1):
            rel = f.relative_to(root_dir)
            out.write(f"{idx:02d}. {rel}\n")
        out.write("\n" + "=" * 80 + "\n\n")

        # File Contents with Delimiters
        out.write("### 3. SOURCE FILE CONTENTS:\n\n")
        for f in collected_files:
            rel = f.relative_to(root_dir)
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as inf:
                    content = inf.read()
                    line_count = len(content.splitlines())
                    total_lines += line_count

                    out.write(f"--- START OF FILE {rel} ---\n\n")
                    out.write(content.rstrip() + "\n\n")
                    out.write(f"--- END OF FILE {rel} ---\n\n")
            except Exception as e:
                out.write(f"# [Error reading {rel}: {e}]\n\n")

    return collected_files, skipped_files, total_lines


def main():
    parser = argparse.ArgumentParser(description="Bundle project codebase into a single AI prompt context file.")
    parser.add_argument("-o", "--output", default="codebase_context.txt", help="Output file path (default: codebase_context.txt)")
    parser.add_argument("-d", "--dir", default=".", help="Root project directory (default: current directory)")
    parser.add_argument("--max-size", type=int, default=500, help="Maximum file size in KB to include (default: 500)")

    args = parser.parse_args()
    root_path = Path(args.dir)
    output_path = Path(args.output).resolve()

    print(f"[*] Scanning codebase in: {root_path.resolve()}...")
    files, skipped, total_lines = bundle_project(root_path, output_path, args.max_size)

    py_count = sum(1 for f in files if f.suffix.lower() == ".py")
    file_size_kb = output_path.stat().st_size / 1024

    print("\n[+] Bundling complete!")
    print(f"    - Output: {output_path}")
    print(f"    - Files included: {len(files)} ({py_count} Python files)")
    print(f"    - Total lines of code: {total_lines:,}")
    print(f"    - Bundle size: {file_size_kb:.1f} KB")

    if skipped:
        print(f"\n[!] Skipped {len(skipped)} eligible text file(s):")
        for path, reason in skipped:
            print(f"    - {path} ({reason})")


if __name__ == "__main__":
    main()
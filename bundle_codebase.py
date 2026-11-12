#!/usr/bin/env python3
"""
bundle_codebase.py
Condenses the emulator codebase into a single AI-friendly context document.
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Directories to skip completely (large assets, caches, virtualenvs)
DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", "ENV",
    "models", "screenshots", "exports", "replays", "states",
    "saves", "translations", "build", "dist", ".idea", ".vscode"
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

# Files that should never be bundled (sensitive secrets, self, output)
DEFAULT_IGNORE_FILES = {
    ".env", ".env.local", ".env.production",
    "bundle_codebase.py"
}

# File extensions prioritized for inclusion
VALID_TEXT_EXTENSIONS = {
    ".py", ".json", ".txt", ".md", ".yml", ".yaml", ".ini", ".cfg", ".toml", ".gitignore", ".sh", ".bat", ".ps1"
}


def is_binary_file(filepath: Path) -> bool:
    """Quick heuristic check to ensure binary content is never written into text context."""
    try:
        with open(filepath, "tr") as check_file:
            check_file.read(1024)
            return False
    except UnicodeDecodeError:
        return True
    except Exception:
        return True


def build_tree_structure(root_dir: Path, collected_files: list[Path]) -> str:
    """Builds a visual directory tree showing only bundled files."""
    tree_lines = [f"{root_dir.resolve().name}/"]
    rel_paths = sorted([f.relative_to(root_dir) for f in collected_files])

    for p in rel_paths:
        parts = p.parts
        indent = "  " * len(parts)
        tree_lines.append(f"{indent}├── {parts[-1]}")

    return "\n".join(tree_lines)


def bundle_project(root_dir: Path, output_file: Path, max_file_size_kb: int = 500):
    root_dir = root_dir.resolve()
    collected_files: list[Path] = []

    # 1. Walk directory and filter eligible text files
    for root, dirs, files in os.walk(root_dir):
        # In-place modify dirs to skip ignored folders recursively
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

        for file_name in files:
            file_path = Path(root) / file_name

            # Skip self, output file, and secret files
            if file_name in DEFAULT_IGNORE_FILES or file_path.resolve() == output_file.resolve():
                continue

            # Skip by extension
            ext = file_path.suffix.lower()
            if ext in DEFAULT_IGNORE_EXTENSIONS:
                continue

            # Target source code and config extensions (or files like .gitignore)
            if ext not in VALID_TEXT_EXTENSIONS and file_name != ".gitignore":
                continue

            # Skip oversized files (> 500 KB)
            try:
                if file_path.stat().st_size > (max_file_size_kb * 1024):
                    continue
            except OSError:
                continue

            # Final check against binary content
            if is_binary_file(file_path):
                continue

            collected_files.append(file_path)

    collected_files.sort(key=lambda p: (str(p.parent), p.name))

    # 2. Write formatted AI Context document
    total_lines = 0
    file_stats = []

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
                    file_stats.append((str(rel), line_count))

                    out.write(f"--- START OF FILE {rel} ---\n\n")
                    out.write(content.rstrip() + "\n\n")
                    out.write(f"--- END OF FILE {rel} ---\n\n")
            except Exception as e:
                out.write(f"# [Error reading {rel}: {e}]\n\n")

    return collected_files, total_lines


def main():
    parser = argparse.ArgumentParser(description="Bundle project codebase into a single AI prompt context file.")
    parser.add_argument("-o", "--output", default="codebase_context.txt", help="Output file path (default: codebase_context.txt)")
    parser.add_argument("-d", "--dir", default=".", help="Root project directory (default: current directory)")
    parser.add_argument("--max-size", type=int, default=500, help="Maximum file size in KB to include (default: 500)")

    args = parser.parse_args()
    root_path = Path(args.dir)
    output_path = Path(args.output).resolve()

    print(f"[*] Scanning codebase in: {root_path.resolve()}...")
    files, total_lines = bundle_project(root_path, output_path, args.max_size)

    file_size_kb = output_path.stat().st_size / 1024
    print("\n[+] Bundling complete!")
    print(f"    - Output: {output_path}")
    print(f"    - Files included: {len(files)}")
    print(f"    - Total lines of code: {total_lines:,}")
    print(f"    - Bundle size: {file_size_kb:.1f} KB")


if __name__ == "__main__":
    main()
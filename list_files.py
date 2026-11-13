from pathlib import Path

IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".idea",
    ".vscode",
}

IGNORE_FILES = {
    ".DS_Store",
    "Thumbs.db",
}


def build_tree(
    directory: Path,
    prefix: str = "",
    stats: dict[str, int] | None = None,
    output_path: Path | None = None,
) -> list[str]:
    if stats is None:
        stats = {"dirs": 0, "files": 0}

    lines = []

    try:
        entries = list(directory.iterdir())
    except PermissionError:
        return [f"{prefix}└── [Permission Denied]"]

    # Filter out ignored directories, ignored files, and the exported file itself
    filtered = [
        entry
        for entry in entries
        if entry.absolute() != output_path
        and not (
            (entry.is_dir() and entry.name in IGNORE_DIRS)
            or (not entry.is_dir() and entry.name in IGNORE_FILES)
        )
    ]

    # Sort: directories first, then files (case-insensitive alphabetical)
    filtered.sort(key=lambda e: (not e.is_dir(), e.name.lower()))

    for index, entry in enumerate(filtered):
        is_last = index == len(filtered) - 1
        connector = "└── " if is_last else "├── "

        if entry.is_dir() and not entry.is_symlink():
            stats["dirs"] += 1
            lines.append(f"{prefix}{connector}{entry.name}/")
            extension = "    " if is_last else "│   "
            lines.extend(build_tree(entry, prefix + extension, stats, output_path))
        else:
            stats["files"] += 1
            lines.append(f"{prefix}{connector}{entry.name}")

    return lines


def export_tree(root_dir: str = ".", output_file: str = "codebase_structure.txt") -> None:
    root = Path(root_dir).resolve()
    out_path = Path(output_file).resolve()
    stats = {"dirs": 0, "files": 0}

    tree_lines = [f"{root.name}/"]
    tree_lines.extend(build_tree(root, stats=stats, output_path=out_path))
    tree_lines.append("")
    tree_lines.append(f"Total: {stats['dirs']} directories, {stats['files']} files")

    output_text = "\n".join(tree_lines)
    out_path.write_text(output_text, encoding="utf-8")

    print(output_text)
    print(f"\nExported to: {out_path}")


if __name__ == "__main__":
    export_tree(root_dir=".", output_file="codebase_structure.txt")
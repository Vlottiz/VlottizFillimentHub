"""Where Filament Forge keeps your data.

Everything (filament log, profiles, photos, backups, and the downloaded AI
engine and model) lives in one per-user folder *outside* the app, so you can
replace or update the app (zip, git or .exe) without losing anything or
downloading the AI again:

    Windows  %LOCALAPPDATA%\\FilamentForge
    macOS    ~/Library/Application Support/FilamentForge
    Linux    ~/.local/share/filament-forge   (or $XDG_DATA_HOME/filament-forge)

Set FORGE_DATA_DIR to use a different folder.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("FORGE_DATA_DIR")
    if override:
        return Path(override)
    home = Path.home()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "FilamentForge" if base else home / "FilamentForge"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "FilamentForge"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "filament-forge"


def app_dir() -> Path:
    """Folder the app runs from (the .exe's folder when packaged)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def migrate_old_data(old: Path, new: Path) -> list[str]:
    """Move data from the old in-app 'data' folder to the per-user folder.

    Only runs when the new folder has no database yet, so it never overwrites
    anything. Returns what was moved (for a message at startup).
    """
    try:
        if old.resolve() == new.resolve() or not (old / "forge.db").is_file() or (new / "forge.db").exists():
            return []
    except OSError:
        return []
    new.mkdir(parents=True, exist_ok=True)
    moved = []
    for item in old.iterdir():
        target = new / item.name
        if target.exists():
            continue
        shutil.move(str(item), str(target))  # a rename on the same drive, so the AI model moves instantly
        moved.append(item.name)
    return moved

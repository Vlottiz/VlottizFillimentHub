"""Read and write OrcaSlicer filament presets.

Orca stores per-filament values as arrays of strings (one entry per extruder
or extruder variant), e.g. "nozzle_temperature": ["220"]. Metadata keys
(type, name, inherits, from, instantiation) are plain strings.

A user preset normally `inherits` a system preset and only lists what it
changes. That is what breaks when an Orca update renames a system preset, so
this app always keeps its own full copy of every setting, and the exported
file also lists every setting it knows about rather than relying on the
parent for them.
"""

from __future__ import annotations

import json
import os
import platform
import re
import time
from pathlib import Path

# Orca's own system presets to inherit from (they exist in OrcaFilamentLibrary).
ORCA_PARENT = {
    "PLA": "Generic PLA @System",
    "PLA+": "Generic PLA @System",
    "PLA Silk": "Generic PLA Silk @System",
    "PLA-CF": "Generic PLA-CF @System",
    "PETG": "Generic PETG @System",
    "PETG-CF": "Generic PETG-CF @System",
    "ABS": "Generic ABS @System",
    "ASA": "Generic ASA @System",
    "TPU": "Generic TPU @System",
    "PA": "Generic PA @System",
    "PA-CF": "Generic PA-CF @System",
    "PC": "Generic PC @System",
}

# Keys we never copy into the settings dict when importing.
_META_KEYS = {
    "type", "name", "inherits", "from", "setting_id", "filament_id",
    "instantiation", "version", "is_custom_defined", "renamed_from",
    "filament_settings_id", "compatible_printers", "compatible_printers_condition",
    "compatible_prints", "compatible_prints_condition", "filament_extruder_variant",
}

_SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    return _SAFE_NAME.sub("_", name).strip(" .") or "filament"


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s or "0"
    return str(v)


def to_preset(name: str, settings: dict, material: str | None = None,
              inherits: str | None = None, compatible_printers: list[str] | None = None) -> dict:
    """Build an Orca user filament preset dict."""
    if inherits is None:
        inherits = ORCA_PARENT.get(material or "", "")
    preset: dict = {
        "type": "filament",
        "name": name,
        "inherits": inherits,
        "from": "User",
        "instantiation": "true",
        "filament_settings_id": [name],
    }
    if compatible_printers:
        preset["compatible_printers"] = list(compatible_printers)
    for key in sorted(settings):
        val = settings[key]
        if val is None or val == "":
            continue
        preset[key] = [_fmt(val)]
    return preset


def info_file(updated: int | None = None) -> str:
    """Companion .info file Orca writes next to each user preset."""
    return (
        "sync_info = \n"
        "user_id = \n"
        "setting_id = \n"
        "base_id = \n"
        f"updated_time = {int(updated or time.time())}\n"
    )


def _parse_scalar(s: str):
    if s in ("nil", ""):
        return None
    try:
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d*\.\d+(e-?\d+)?|-?\d+e-?\d+", s):
            return float(s)
    except ValueError:
        pass
    return s


def from_preset(preset: dict) -> dict:
    """Flatten an Orca preset into {settings, name, inherits, material}."""
    settings = {}
    for key, val in preset.items():
        if key in _META_KEYS:
            continue
        if isinstance(val, list):
            if not val or not all(isinstance(x, str) for x in val):
                continue
            val = val[0]
        if not isinstance(val, str):
            continue
        if "gcode" in key:
            continue  # keep imports to tuning values
        parsed = _parse_scalar(val)
        if parsed is not None:
            settings[key] = parsed
    material = settings.get("filament_type")
    return {
        "name": preset.get("name", "Imported filament"),
        "inherits": preset.get("inherits", ""),
        "material": material if isinstance(material, str) else None,
        "settings": settings,
    }


def config_dir() -> Path | None:
    """Default OrcaSlicer config folder for this OS (may not exist)."""
    override = os.environ.get("ORCA_CONFIG_DIR")
    if override:
        return Path(override)
    system = platform.system()
    home = Path.home()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        return Path(base) / "OrcaSlicer" if base else None
    if system == "Darwin":
        return home / "Library" / "Application Support" / "OrcaSlicer"
    return home / ".config" / "OrcaSlicer"


def user_filament_dirs(root: Path | None = None) -> list[Path]:
    """All user/<id>/filament folders. 'default' is used when not logged in."""
    root = root or config_dir()
    if not root:
        return []
    user = root / "user"
    if not user.is_dir():
        return []
    return sorted(p / "filament" for p in user.iterdir()
                  if p.is_dir() and (p / "filament").is_dir())


def scan(root: Path | None = None) -> list[dict]:
    """List user filament presets found in Orca's folder."""
    found = []
    for d in user_filament_dirs(root):
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("type", "filament") != "filament":
                continue
            found.append({
                "path": str(f),
                "account": d.parent.name,
                "name": data.get("name", f.stem),
                "inherits": data.get("inherits", ""),
                "modified": int(f.stat().st_mtime),
            })
    return found


def install(preset: dict, target_dir: Path) -> Path:
    """Write preset + .info into an Orca user filament folder."""
    target_dir.mkdir(parents=True, exist_ok=True)
    base = safe_filename(preset["name"])
    path = target_dir / f"{base}.json"
    path.write_text(json.dumps(preset, indent=4), encoding="utf-8")
    (target_dir / f"{base}.info").write_text(info_file(), encoding="utf-8")
    return path

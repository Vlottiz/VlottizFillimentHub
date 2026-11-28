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

import io
import json
import os
import platform
import re
import time
import zipfile
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

# Orca skips user presets without a parseable "version", so always write one.
PRESET_VERSION = "2.0.0.0"

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
        "version": PRESET_VERSION,
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


def active_user_dir(root: Path | None = None) -> Path | None:
    """The user/<folder>/filament folder Orca currently loads presets from.

    Orca reads presets from user/default when you're not logged in and from
    your account folder when you are; its config file records which
    ("preset_folder"). Falls back to user/default.
    """
    root = root or config_dir()
    if not root or not root.is_dir():
        return None
    folder = None
    conf = root / "OrcaSlicer.conf"
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'"preset_folder"\s*:\s*"([^"]*)"', text)
        if m and m.group(1).strip():
            folder = m.group(1).strip()
    except OSError:
        pass
    if not folder or not re.fullmatch(r"[\w.-]+", folder):
        folder = "default"
    return root / "user" / folder / "filament"


def install_dirs(root: Path | None = None) -> list[Path]:
    """Folders the app may install into: the active one first, then any others."""
    out = []
    active = active_user_dir(root)
    if active:
        out.append(active)
    out += [d for d in user_filament_dirs(root) if d != active]
    return out


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


# ------------------------------------------------- your own group in Orca
DEFAULT_GROUP = {"name": "VlottizFF", "as_vendor": True, "prefix_names": True}


def group_name(name: str, group: dict | None) -> str:
    """Preset name as exported, e.g. 'VlottizFF Giantarm PLA Silk @Snapmaker U1'."""
    g = (group or {}).get("name", "").strip()
    if not g or not (group or {}).get("prefix_names"):
        return name
    return name if name.lower().startswith(g.lower() + " ") else f"{g} {name}"


def strip_group(name: str, group: dict | None) -> str:
    g = (group or {}).get("name", "").strip()
    if g and name.lower().startswith(g.lower() + " "):
        return name[len(g) + 1:].strip()
    return name


def split_printer(name: str) -> tuple[str, str | None]:
    """'Giantarm PLA @Snapmaker U1' -> ('Giantarm PLA', 'Snapmaker U1')."""
    if " @" in name:
        base, printer = name.rsplit(" @", 1)
        return base.strip(), printer.strip() or None
    return name.strip(), None


COLOUR_WORDS = {
    "black", "white", "grey", "gray", "silver", "gold", "red", "blue", "green", "yellow", "orange", "purple",
    "pink", "brown", "beige", "ivory", "clear", "transparent", "natural", "navy", "teal", "cyan", "magenta",
    "copper", "bronze", "rainbow", "galaxy", "dark", "light", "sky", "olive", "mint", "violet", "rose",
    "matte", "translucent", "glow", "marble", "wood", "jade", "lavender", "turquoise", "charcoal", "cream",
}


def filament_from_preset(name: str, settings: dict, group: dict | None = None) -> dict:
    """Work out a filament log entry (brand, name, colour, specs) from a preset."""
    base, printer = split_printer(strip_group(name, group))
    vendor = settings.get("filament_vendor")
    gname = (group or {}).get("name", "")
    if not isinstance(vendor, str) or vendor.lower() in ("generic", gname.lower(), ""):
        vendor = None
    words = base.split()
    if not vendor and len(words) > 1:
        vendor = words[0]  # usually "Brand Material ..."
    if vendor and base.lower().startswith(vendor.lower() + " "):
        base = base[len(vendor) + 1:]
    colour_words = []
    words = base.split()
    while len(words) > 1 and words[-1].lower() in COLOUR_WORDS:
        colour_words.insert(0, words.pop())
    base = " ".join(words)
    spec = {}
    for src, dst in (("nozzle_temperature_range_low", "nozzle_min"), ("nozzle_temperature_range_high", "nozzle_max"),
                     ("filament_density", "density"), ("filament_max_volumetric_speed", "max_volumetric_speed")):
        if isinstance(settings.get(src), (int, float)):
            spec[dst] = settings[src]
    bed = settings.get("textured_plate_temp") or settings.get("hot_plate_temp")
    if isinstance(bed, (int, float)) and bed > 0:
        spec["bed_min"] = spec["bed_max"] = bed
    colour = settings.get("default_filament_colour")
    cost = settings.get("filament_cost")
    return {
        "brand": vendor,
        "name": base.strip() or name,
        "colour_name": " ".join(colour_words) or None,
        "colour_hex": colour if isinstance(colour, str) and colour.startswith("#") else None,
        "diameter": settings.get("filament_diameter") or 1.75,
        "price": cost if isinstance(cost, (int, float)) and cost > 0 else None,
        "weight_g": 1000 if isinstance(cost, (int, float)) and cost > 0 else None,
        "spec": spec,
        "printer_hint": printer,
    }


# ------------------------------------------------------- preset bundles
# Orca's "Bundle presets": a zip (.orca_filament) holding bundle_structure.json
# plus one preset .json per filament. File > Import > Import Configs installs it
# into user/<account>/_local/<id>/ and lists it under its own name (like OrcaGen).
# Format taken from OrcaSlicer's PresetBundle::import_presets / BundleMetadata.
BUNDLE_EXT = ".orca_filament"


def bundle_id(name: str) -> str:
    """Stable id, so importing a newer bundle replaces the old one."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "filament-forge"


def make_bundle(presets: list[dict], name: str, author: str = "", description: str = "") -> bytes:
    """Build an Orca filament bundle (zip bytes) from filament presets."""
    files: dict[str, dict] = {}
    for p in presets:
        base = safe_filename(p["name"])
        fname, n = base + ".json", 2
        while fname.lower() in {f.lower() for f in files}:
            fname, n = f"{base} ({n}).json", n + 1
        files[fname] = p
    now = int(time.time())
    manifest = {
        "id": bundle_id(name),
        "name": name,
        "version": time.strftime("%Y.%m.%d.%H%M", time.localtime(now)),
        "description": description or f"{name} filament presets from Filament Forge",
        "author": author,
        "updated_time": now,
        "print_presets": [],
        "filament_presets": list(files),
        "printer_presets": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bundle_structure.json", json.dumps(manifest, indent=4))
        for fname, p in files.items():
            z.writestr(fname, json.dumps(p, indent=4))
    return buf.getvalue()

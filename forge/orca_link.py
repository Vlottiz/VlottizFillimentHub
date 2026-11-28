"""Read what OrcaSlicer has set up: its printers and what's in each filament slot.

Orca keeps its printer presets as JSON files (system ones under ``system/<vendor>/machine``,
yours under ``user/<account>/machine``) and remembers which filament preset is in each slot,
per printer, in ``OrcaSlicer.conf``. Orca rewrites that file within a second or so of a
change, so reading it again shows changes made in Orca without restarting anything.

Only a few keys are read from OrcaSlicer.conf; it also holds things like printer access
codes, which this module never touches.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import orca

MAX_DEPTH = 12  # inherits chains are short; this stops a loop in broken files


def read_conf(root: Path | None = None) -> dict:
    root = root or orca.config_dir()
    if not root:
        return {}
    try:
        text = (root / "OrcaSlicer.conf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    # Orca may add a "# MD5 checksum" line after the JSON.
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def conf_mtime(root: Path | None = None) -> float:
    root = root or orca.config_dir()
    try:
        return (root / "OrcaSlicer.conf").stat().st_mtime if root else 0.0
    except OSError:
        return 0.0


def _split_colours(v) -> list[str]:
    if isinstance(v, list):
        return [str(c) for c in v]
    return [c.strip() for c in str(v or "").split(",") if c.strip()]


def _slots_from(entry: dict) -> list[str]:
    """Slot 1 is stored as 'filament', slots 2.. as 'filament_01', 'filament_02', ..."""
    first = entry.get("filament")
    if not first:
        return []
    slots = [str(first)]
    i = 1
    while entry.get(f"filament_{i:02d}"):
        slots.append(str(entry[f"filament_{i:02d}"]))
        i += 1
    return slots


def selection(conf: dict) -> dict:
    """The printer selected in Orca now, and the filament preset in each of its slots."""
    presets = conf.get("presets") or {}
    printer = presets.get("machine") or ""
    by_printer = printer_slots(conf)
    slots = by_printer.get(printer, {})
    if not slots.get("filaments") and isinstance(presets.get("filaments"), list):
        slots = {"filaments": [str(x) for x in presets["filaments"]],
                 "colours": _split_colours(presets.get("filament_colors"))}
    return {"printer": printer, **{"filaments": [], "colours": []}, **slots}


def printer_slots(conf: dict) -> dict[str, dict]:
    """Every printer Orca remembers, with its slots: {printer: {filaments, colours}}."""
    out = {}
    for entry in conf.get("orca_presets") or []:
        if isinstance(entry, dict) and entry.get("machine"):
            out[str(entry["machine"])] = {"filaments": _slots_from(entry),
                                          "colours": _split_colours(entry.get("filament_colors"))}
    return out


# ------------------------------------------------------------ printer presets
def machine_files(root: Path | None = None) -> dict[str, tuple[Path, bool]]:
    """{preset name: (file, is_user)} for every printer preset Orca has."""
    root = root or orca.config_dir()
    found: dict[str, tuple[Path, bool]] = {}
    if not root:
        return found
    dirs: list[tuple[Path, bool]] = []
    system = root / "system"
    if system.is_dir():
        dirs += [(v / "machine", False) for v in sorted(system.iterdir()) if (v / "machine").is_dir()]
    user = root / "user"
    if user.is_dir():
        dirs += [(a / "machine", True) for a in sorted(user.iterdir()) if (a / "machine").is_dir()]
    for d, is_user in dirs:
        for f in sorted(d.glob("*.json")):
            name = None
            try:
                head = f.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
                name = json.loads(f'"{m.group(1)}"') if m else f.stem
            except (OSError, ValueError):
                continue
            # a user preset of the same name wins over the system one
            if name not in found or is_user:
                found[name] = (f, is_user)
    return found


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve(name: str, files: dict[str, tuple[Path, bool]]) -> dict:
    """A printer preset with everything it inherits filled in."""
    chain = []
    seen = set()
    cur = name
    while cur and cur not in seen and len(chain) < MAX_DEPTH and cur in files:
        seen.add(cur)
        data = _load(files[cur][0])
        chain.append(data)
        cur = data.get("inherits")
    merged: dict = {}
    for data in reversed(chain):
        merged.update(data)
    if chain:
        merged["name"] = name
    return merged


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _num(v) -> float | None:
    v = _first(v)
    try:
        n = float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return int(n) if n.is_integer() else n


def _bed(printable_area) -> tuple[float | None, float | None]:
    pts = []
    for p in printable_area or []:
        try:
            x, y = str(p).lower().split("x")
            pts.append((float(x), float(y)))
        except ValueError:
            continue
    if not pts:
        return None, None
    w = max(x for x, _ in pts) - min(x for x, _ in pts)
    h = max(y for _, y in pts) - min(y for _, y in pts)
    tidy = lambda n: int(n) if float(n).is_integer() else round(n, 1)
    return tidy(w), tidy(h)


KINEMATICS = {"corexy": "corexy", "i3": "cartesian", "hbot": "hbot", "delta": "delta"}


def printer_fields(cfg: dict) -> dict:
    """Map an Orca printer preset onto the app's printer record (only what's known)."""
    nozzles = cfg.get("nozzle_diameter")
    count = len(nozzles) if isinstance(nozzles, list) and nozzles else None
    speeds = [_num(cfg.get(k)) for k in ("machine_max_speed_x", "machine_max_speed_y")]
    speeds = [s for s in speeds if s]
    bx, by = _bed(cfg.get("printable_area"))
    fields = {
        "orca_printer": cfg.get("name"),
        "toolheads": count,
        "nozzle_diameter": _num(nozzles),
        "max_velocity": min(speeds) if speeds else None,
        "max_accel": _num(cfg.get("machine_max_acceleration_extruding")),
        "kinematics": KINEMATICS.get(str(cfg.get("printer_structure") or "").lower()),
        "bed_x": bx, "bed_y": by,
        "bed_z": _num(cfg.get("printable_height")),
        "orca_model": cfg.get("printer_model") or None,
    }
    return {k: v for k, v in fields.items() if v not in (None, "", 0)}


def printers(root: Path | None = None) -> list[dict]:
    """Printers to offer: the ones you've used in Orca and your own printer presets.

    System presets you never added in Orca are left out (there are hundreds).
    """
    root = root or orca.config_dir()
    conf = read_conf(root)
    files = machine_files(root)
    current = (conf.get("presets") or {}).get("machine") or ""
    names = list(printer_slots(conf))
    names += [n for n, (_, is_user) in files.items() if is_user]
    if current:
        names.insert(0, current)
    out, seen = [], set()
    for n in names:
        if n in seen or n not in files:
            continue
        seen.add(n)
        cfg = resolve(n, files)
        out.append({"name": n, "current": n == current, "user": files[n][1],
                    "fields": printer_fields(cfg), "modified": files[n][0].stat().st_mtime})
    return out

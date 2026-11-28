"""Spools you own and how much filament is left on each.

Orca writes how much filament a print uses into its G-code, one number per filament slot:
    ; filament used [g] = 12.34, 0, 3.21
    ; filament used [mm] = 4100.2, 0, 1066.5
    ; filament_settings_id = "VlottizFF Giantarm PLA";"Generic PETG"
Filament Forge reads those lines (Orca's post-processing option runs it after every slice
you export or send) and takes the grams off the spool loaded in that slot.
"""

from __future__ import annotations

import math
import re

HEAD = 400_000  # Orca puts the summary near the top and the settings at the end
TAIL = 600_000
DEFAULT_DENSITY = 1.24


def _floats(s: str) -> list[float]:
    out = []
    for part in re.split(r"[,;]", s):
        part = part.strip()
        try:
            out.append(float(part))
        except ValueError:
            out.append(0.0)
    return out


def _names(s: str) -> list[str]:
    s = s.strip()
    if '"' in s:
        return re.findall(r'"((?:[^"\\]|\\.)*)"', s)
    return [x.strip() for x in s.split(";") if x.strip()]


def read_gcode_bytes(raw: bytes) -> str:
    """Just the parts of a G-code file that carry the summary and settings."""
    if len(raw) > HEAD + TAIL:
        raw = raw[:HEAD] + b"\n" + raw[-TAIL:]
    return raw.decode("utf-8", errors="replace")


def parse_usage(text: str) -> dict:
    """Filament used per slot (1-based) from Orca G-code text."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith(";"):
            continue
        m = re.match(r";\s*([^=]+?)\s*=\s*(.*)$", line)
        if m:
            found.setdefault(m.group(1).strip().lower(), m.group(2))
    grams = _floats(found["filament used [g]"]) if "filament used [g]" in found else []
    mm = _floats(found["filament used [mm]"]) if "filament used [mm]" in found else []
    presets = _names(found.get("filament_settings_id", ""))
    colours = [c.strip() for c in found.get("filament_colour", "").split(";") if c.strip()]
    density = _floats(found["filament_density"]) if "filament_density" in found else []
    diameter = _floats(found["filament_diameter"]) if "filament_diameter" in found else []
    n = max(len(grams), len(mm))
    slots = []
    for i in range(n):
        g = grams[i] if i < len(grams) else 0.0
        if not g and i < len(mm) and mm[i]:
            # no weight written (no density set): work it out from length
            d = diameter[i] if i < len(diameter) and diameter[i] else 1.75
            rho = density[i] if i < len(density) and density[i] else DEFAULT_DENSITY
            g = mm[i] * math.pi * (d / 2) ** 2 * rho / 1000
        if g <= 0:
            continue
        slots.append({"slot": i + 1, "grams": round(g, 2),
                      "mm": round(mm[i], 1) if i < len(mm) else None,
                      "preset": presets[i] if i < len(presets) else None,
                      "colour": colours[i] if i < len(colours) else None})
    return {
        "slots": slots,
        "total_g": round(sum(s["grams"] for s in slots), 2),
        "printer": (_names(found.get("printer_settings_id", "")) or [None])[0],
        "print_time": found.get("estimated printing time (normal mode)"),
    }


def spool_status(spool: dict) -> dict:
    """How full a spool is, for showing."""
    net = spool.get("net_g") or 0
    left = spool.get("remaining_g")
    pct = round(100 * left / net) if net and left is not None else None
    return {"percent": max(0, min(100, pct)) if pct is not None else None,
            "low": left is not None and left < 100}


def from_weighing(gross_g: float, tare_g: float | None) -> float:
    """Filament left from a scale reading of the spool (spool + filament)."""
    return round(max(0.0, gross_g - (tare_g or 0)), 1)

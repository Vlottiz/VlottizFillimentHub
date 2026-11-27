"""Read the parts of a Klipper config that matter for filament tuning.

Handles the Klipper quirks a plain INI parser trips over: `key: value` and
`key = value`, `#`/`;` comments, indented continuation lines (gcode macros),
and the `#*# <---- SAVE_CONFIG ---->` block at the bottom, whose values
override the ones above it (that is where calibrated values live).

You can pass several files (printer.cfg plus its [include]s); later files
override earlier ones, like Klipper does.
"""

from __future__ import annotations

import re

_SECTION = re.compile(r"^\[([^\]]+)\]")
_KEYVAL = re.compile(r"^([A-Za-z0-9_\-.]+)\s*[:=]\s*(.*)$")


def _strip_comment(line: str) -> str:
    # Klipper treats # and ; as comments when at line start or after whitespace.
    m = re.search(r"(^|\s)[#;]", line)
    return line[: m.start()].rstrip() if m else line.rstrip()


def parse_sections(text: str) -> dict[str, dict[str, str]]:
    """Parse Klipper config text into {section: {key: value}}."""
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    last_key: str | None = None

    lines = text.splitlines()
    normal, saved = [], []
    in_saved = False
    for raw in lines:
        if raw.startswith("#*#"):
            in_saved = True
            body = raw[3:]
            saved.append(body[1:] if body.startswith(" ") else body)
        elif not in_saved:
            normal.append(raw)

    for block in (normal, saved):
        current, last_key = None, None
        for raw in block:
            if "SAVE_CONFIG" in raw and "<--" in raw:
                continue
            if not raw.strip():
                last_key = None
                continue
            if raw[0] in " \t":
                # continuation line (multi-line values such as gcode)
                if current and last_key:
                    cont = _strip_comment(raw).strip()
                    if cont:
                        prev = sections[current][last_key]
                        sections[current][last_key] = (prev + "\n" + cont).strip()
                continue
            line = _strip_comment(raw)
            if not line:
                continue
            sm = _SECTION.match(line)
            if sm:
                current = " ".join(sm.group(1).split()).lower()
                sections.setdefault(current, {})
                last_key = None
                continue
            km = _KEYVAL.match(line)
            if km and current is not None:
                last_key = km.group(1).lower()
                sections[current][last_key] = km.group(2).strip()
    return sections


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.split()[0].split(",")[0])
    except (ValueError, IndexError):
        return None


_EXTRUDER_SECTION = re.compile(r"^extruder(\d*)$")


def summarize(texts: list[str]) -> dict:
    """Merge one or more config files and pull out tuning-relevant limits."""
    merged: dict[str, dict[str, str]] = {}
    for t in texts:
        for sec, kv in parse_sections(t).items():
            merged.setdefault(sec, {}).update(kv)

    printer = merged.get("printer", {})
    out: dict = {
        "kinematics": printer.get("kinematics"),
        "max_velocity": _num(printer.get("max_velocity")),
        "max_accel": _num(printer.get("max_accel")),
        "square_corner_velocity": _num(printer.get("square_corner_velocity")),
        "max_z_velocity": _num(printer.get("max_z_velocity")),
        "bed_max_temp": _num(merged.get("heater_bed", {}).get("max_temp")),
        "bed_x": _num(merged.get("stepper_x", {}).get("position_max")),
        "bed_y": _num(merged.get("stepper_y", {}).get("position_max")),
        "bed_z": _num(merged.get("stepper_z", {}).get("position_max")),
        "extruders": [],
        "sections": sorted(merged),
    }

    for sec in sorted(merged, key=lambda s: (len(s), s)):
        m = _EXTRUDER_SECTION.match(sec)
        if not m:
            continue
        kv = merged[sec]
        out["extruders"].append({
            "name": sec,
            "nozzle_diameter": _num(kv.get("nozzle_diameter")),
            "max_temp": _num(kv.get("max_temp")),
            "max_extrude_cross_section": _num(kv.get("max_extrude_cross_section")),
            "max_extrude_only_velocity": _num(kv.get("max_extrude_only_velocity")),
            "pressure_advance": _num(kv.get("pressure_advance")),
            "filament_diameter": _num(kv.get("filament_diameter")),
        })
    return out


def to_printer_fields(summary: dict) -> dict:
    """Map a config summary onto the app's printer record (only known values)."""
    ex = summary.get("extruders") or []
    first = ex[0] if ex else {}
    temps = [e["max_temp"] for e in ex if e.get("max_temp")]
    fields = {
        "kinematics": summary.get("kinematics"),
        "max_velocity": summary.get("max_velocity"),
        "max_accel": summary.get("max_accel"),
        "square_corner_velocity": summary.get("square_corner_velocity"),
        "bed_max_temp": summary.get("bed_max_temp"),
        "bed_x": summary.get("bed_x"),
        "bed_y": summary.get("bed_y"),
        "bed_z": summary.get("bed_z"),
        "nozzle_diameter": first.get("nozzle_diameter"),
        # Klipper refuses to heat past max_temp; use the lowest across toolheads.
        "nozzle_max_temp": min(temps) if temps else None,
        "max_extrude_cross_section": first.get("max_extrude_cross_section"),
        "toolheads": len(ex) or None,
    }
    return {k: v for k, v in fields.items() if v is not None}

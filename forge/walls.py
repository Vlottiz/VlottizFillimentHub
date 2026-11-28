"""Per-filament wall settings.

Orca applies wall speeds and acceleration from the print (process) settings,
not the filament preset, so these can't go into the filament preset itself.
The app keeps them per filament, checks them against what the filament can
melt, and writes them into the preset's Notes so you can see them in Orca.

Stored on the profile as {"values": {...}, "sources": {...}, "note": "..."}.
Sources: manual (you set), vfa (your VFA test), fix (a Fix-a-print-problem
trial), tested (a trial you confirmed), imported.
"""

from __future__ import annotations

import re

FIELDS = [
    # key, label, unit, (min, max), Orca location
    ("outer_wall_speed", "Outer wall speed", "mm/s", (5, 1000), "Speed → Outer wall"),
    ("inner_wall_speed", "Inner wall speed", "mm/s", (5, 1000), "Speed → Inner wall"),
    ("outer_wall_acceleration", "Outer wall acceleration", "mm/s²", (100, 100000), "Speed → Acceleration → Outer wall"),
    ("wall_loops", "Number of walls", "", (1, 20), "Strength → Walls → Wall loops"),
]
KEYS = [f[0] for f in FIELDS]
_TAG = re.compile(r"\[ffw:([^\]]*)\]")


def empty() -> dict:
    return {"values": {}, "sources": {}, "note": ""}


def normalise(w) -> dict:
    w = w if isinstance(w, dict) else {}
    vals = {k: v for k, v in (w.get("values") or {}).items() if k in KEYS and v is not None}
    srcs = {k: v for k, v in (w.get("sources") or {}).items() if k in vals}
    return {"values": vals, "sources": srcs, "note": str(w.get("note") or "")[:500]}


def apply(w: dict, changes: dict, source: str) -> tuple[dict, list[str]]:
    """Set (or clear with None/'') wall values. Returns (new walls, changed keys)."""
    w = normalise(w)
    changed = []
    limits = {k: lim for k, _, _, lim, _ in FIELDS}
    for k, v in changes.items():
        if k not in limits:
            raise ValueError(f"Unknown wall setting: {k}")
        if v is None or v == "":
            if k in w["values"]:
                w["values"].pop(k)
                w["sources"].pop(k, None)
                changed.append(k)
            continue
        try:
            num = float(str(v).replace(",", "."))
        except ValueError:
            raise ValueError(f"{k}: '{v}' isn't a number")
        lo, hi = limits[k]
        if not lo <= num <= hi:
            raise ValueError(f"{k} should be between {lo} and {hi}")
        num = int(round(num)) if k in ("wall_loops", "outer_wall_acceleration") or num.is_integer() else round(num, 1)
        if w["values"].get(k) != num or w["sources"].get(k) != source:
            w["values"][k] = num
            w["sources"][k] = source
            changed.append(k)
    return w, changed


def flow_check(w: dict, settings: dict, printer: dict | None,
               layer_height: float = 0.2, line_width: float | None = None) -> list[dict]:
    """Will the wall speeds ask for more plastic than this filament can melt?"""
    w = normalise(w)
    nozzle = (printer or {}).get("nozzle_diameter") or 0.4
    lw = line_width or round(nozzle * 1.05, 2)
    mvs = settings.get("filament_max_volumetric_speed")
    out = []
    for k in ("outer_wall_speed", "inner_wall_speed"):
        speed = w["values"].get(k)
        if not speed:
            continue
        need = round(speed * layer_height * lw, 1)
        item = {"key": k, "speed": speed, "needs": need, "max": mvs, "ok": True}
        if mvs and need > mvs:
            item["ok"] = False
            item["capped_at"] = int(mvs / (layer_height * lw))
            item["message"] = (f"{speed:g} mm/s needs {need} mm³/s but this filament's max is {mvs} mm³/s, "
                               f"so Orca will slow it to about {item['capped_at']} mm/s.")
        machine = (printer or {}).get("max_velocity")
        if machine and speed > machine:
            item["ok"] = False
            item["message"] = f"{speed:g} mm/s is faster than the printer's max of {machine:g} mm/s."
        out.append(item)
    return out


def notes_line(w: dict) -> str | None:
    """Readable line plus a tag the importer reads back."""
    w = normalise(w)
    if not w["values"] and not w["note"]:
        return None
    labels = {k: (lab, unit) for k, lab, unit, _, _ in FIELDS}
    parts = [f"{labels[k][0].lower()} {v:g}{(' ' + labels[k][1]) if labels[k][1] else ''}"
             for k, v in w["values"].items()]
    text = "Walls (set these in Orca's print settings): " + (", ".join(parts) if parts else "see note")
    if w["note"]:
        text += f". Note: {w['note']}"
    tag = ";".join(f"{k}={w['values'][k]:g}" for k in KEYS if k in w["values"])
    return f"{text}\n[ffw:{tag}]"


def read_tag(notes: str) -> dict:
    """Walls from a [ffw:...] tag in imported Notes (sources = imported)."""
    m = _TAG.search(notes or "")
    if not m:
        return empty()
    changes = {}
    for part in m.group(1).split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() in KEYS:
                changes[k.strip()] = v.strip()
    try:
        w, _ = apply(empty(), changes, "imported")
    except ValueError:
        return empty()
    return w

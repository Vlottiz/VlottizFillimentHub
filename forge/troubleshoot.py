"""Print-problem detector.

The AI (if running) only *diagnoses*: it looks at your photo and description
and picks from the fixed list below, saying how sure it is and what it saw.
The setting changes come from the rules here, never from the model: small,
well-known steps, clamped to the filament's range and the printer's limits,
and at most MAX_CHANGES at a time so you can tell what helped.

Only filament-profile settings are changed. Problems caused by print/process
or printer settings (e.g. ringing) get tips instead.
"""

from __future__ import annotations

import base64
import json
import re

MAX_CHANGES = 2

# change = (setting key, how, amount, why)
#   how: "add" (+amount), "mul" (x amount), "set" (= amount), "min_set" (raise to at least amount)
ISSUES: dict[str, dict] = {
    "stringing": {
        "title": "Stringing / oozing",
        "looks_like": "thin hairs or webs of plastic between separate parts of the print",
        "changes": [
            ("nozzle_temperature", "add", -5, "Cooler plastic oozes less while travelling."),
            ("filament_retraction_length", "add", 0.2, "Pulls the filament back further before travel moves."),
        ],
        "tips": ["Wet filament strings a lot: dry it (see the material's drying temperature).",
                 "Run the Retraction calibration to find the exact value."],
    },
    "under_extrusion": {
        "title": "Under-extrusion",
        "looks_like": "gaps between lines, thin or missing walls, weak spongy parts, holes in top surfaces",
        "changes": [
            ("filament_max_volumetric_speed", "mul", 0.85, "Slows printing so the hotend can keep up."),
            ("nozzle_temperature", "add", 5, "Hotter plastic flows more easily."),
        ],
        "tips": ["Check for a partial clog or a tangled spool.",
                 "Run the Max flowrate and Flow rate calibrations."],
    },
    "over_extrusion": {
        "title": "Over-extrusion",
        "looks_like": "blobby or rough surfaces, ridges on top layers, dimensions too large, nozzle dragging",
        "changes": [("filament_flow_ratio", "add", -0.02, "Pushes slightly less plastic.")],
        "tips": ["Run the Flow rate calibration for an exact value.",
                 "Check the filament diameter setting matches the spool."],
    },
    "warping": {
        "title": "Warping / corners lifting",
        "looks_like": "corners or edges curling up off the bed, a gap under the part",
        "changes": [
            ("textured_plate_temp", "add", 5, "A warmer bed keeps the bottom layers from shrinking."),
            ("hot_plate_temp", "add", 5, "Same for the smooth plate."),
        ],
        "tips": ["Add a brim (process settings).", "Clean the plate with dish soap and water.",
                 "ABS/ASA/PA: close the enclosure and avoid drafts."],
    },
    "poor_adhesion": {
        "title": "First layer not sticking",
        "looks_like": "the first layer peels, lifts, or the print comes loose during printing",
        "changes": [
            ("textured_plate_temp_initial_layer", "add", 5, "A warmer bed helps the first layer grip."),
            ("nozzle_temperature_initial_layer", "add", 5, "A hotter first layer bonds better to the plate."),
        ],
        "tips": ["Clean the plate (fingerprints are the usual cause).", "Check the Z offset / first layer squish."],
    },
    "elephant_foot": {
        "title": "Elephant's foot",
        "looks_like": "the bottom few layers bulge outwards wider than the rest",
        "changes": [
            ("textured_plate_temp_initial_layer", "add", -5, "A cooler bed lets the bottom layers set sooner."),
            ("hot_plate_temp_initial_layer", "add", -5, "Same for the smooth plate."),
        ],
        "tips": ["Use 'Elephant foot compensation' in the process settings (0.1–0.2 mm)."],
    },
    "layer_separation": {
        "title": "Weak layers / layer splitting",
        "looks_like": "cracks between layers, the part snaps easily along a layer line",
        "changes": [
            ("nozzle_temperature", "add", 10, "Hotter plastic fuses layers together better."),
            ("fan_max_speed", "add", -20, "Less cooling keeps layers hot enough to bond."),
        ],
        "tips": ["ABS/ASA/PA: use the enclosure.", "Wet filament makes weak layers: dry it."],
    },
    "bulging_corners": {
        "title": "Bulging corners",
        "looks_like": "corners stick out or have a blob, walls bulge just before a corner",
        "changes": [("pressure_advance", "add", 0.01, "More pressure advance stops plastic piling up at corners.")],
        "tips": ["Run the Pressure advance calibration for an exact value."],
    },
    "gappy_corners": {
        "title": "Gaps or thin spots after corners",
        "looks_like": "a gap, thin line or missing plastic just after a corner or at the start of a line",
        "changes": [("pressure_advance", "add", -0.01, "Less pressure advance stops the nozzle running dry after corners.")],
        "tips": ["Run the Pressure advance calibration for an exact value."],
    },
    "sagging_overhangs": {
        "title": "Drooping overhangs / curling",
        "looks_like": "overhangs or bridges sag, droop, or curl upwards; messy undersides",
        "changes": [
            ("overhang_fan_speed", "min_set", 100, "Full fan on overhangs freezes the plastic in place."),
            ("nozzle_temperature", "add", -5, "Cooler plastic sets faster."),
        ],
        "tips": ["Slow down overhangs or add supports in the process settings."],
    },
    "blobs_zits": {
        "title": "Blobs and zits",
        "looks_like": "small bumps or pimples on the outside walls, often in a line (the seam)",
        "changes": [
            ("nozzle_temperature", "add", -5, "Less ooze when the nozzle stops and starts."),
            ("filament_retraction_length", "add", 0.1, "Slightly more retraction at the seam."),
        ],
        "tips": ["Try 'Aligned' or 'Back' seam position and enable 'Wipe' in the process settings."],
    },
    "rough_top": {
        "title": "Rough or gappy top surface",
        "looks_like": "the top layer has gaps, pillowing (bumps) or a scratchy texture",
        "changes": [
            ("filament_flow_ratio", "add", 0.02, "A little more plastic closes gaps in top layers."),
            ("fan_max_speed", "add", 10, "More cooling stops the top layer sagging into the infill."),
        ],
        "tips": ["Add a top shell layer or use more infill (process settings)."],
    },
    "ringing": {
        "title": "Ringing / ghosting",
        "looks_like": "ripples or echoes of features repeated across a wall after corners or holes",
        "changes": [],
        "tips": ["This comes from speed and acceleration, not the filament: lower outer wall acceleration in the "
                 "process settings, and make sure input shaping is calibrated on the printer.",
                 "Check belts are tight."],
    },
    "wet_filament": {
        "title": "Wet filament",
        "looks_like": "popping or hissing while printing, bubbles, a rough fuzzy surface, lots of stringing",
        "changes": [],
        "tips": ["Dry the spool (see the material's drying temperature and time) and store it with desiccant."],
    },
}

# Never move a value further than these from what it was, whatever the rules say.
LIMITS = {
    "nozzle_temperature": (150, 350), "nozzle_temperature_initial_layer": (150, 350),
    "hot_plate_temp": (0, 120), "hot_plate_temp_initial_layer": (0, 120),
    "textured_plate_temp": (0, 120), "textured_plate_temp_initial_layer": (0, 120),
    "filament_flow_ratio": (0.85, 1.15), "filament_max_volumetric_speed": (1, 60),
    "pressure_advance": (0, 0.2), "filament_retraction_length": (0, 4),
    "fan_max_speed": (0, 100), "fan_min_speed": (0, 100), "overhang_fan_speed": (0, 100),
}

# What a setting starts from when the profile doesn't set it (the printer's default).
UNSET_DEFAULTS = {"filament_retraction_length": 0.8, "pressure_advance": 0.04}


def catalogue() -> list[dict]:
    return [{"id": k, "title": v["title"], "looks_like": v["looks_like"], "tips": v["tips"],
             "changes": bool(v["changes"])} for k, v in ISSUES.items()]


def _round(key: str, v: float):
    if "temp" in key or "fan" in key:
        return int(round(v))
    if key in ("filament_flow_ratio", "pressure_advance"):
        return round(v, 3)
    return round(v, 2)


def plan_changes(issue_ids: list[str], settings: dict, printer: dict | None = None) -> list[dict]:
    """Concrete changes for the diagnosed problems, most likely problem first."""
    printer = printer or {}
    out: dict[str, dict] = {}
    for iid in issue_ids:
        for key, how, amount, why in ISSUES.get(iid, {}).get("changes", []):
            if len(out) >= MAX_CHANGES:
                break
            if key in out:
                continue
            old = settings.get(key)
            base = old if isinstance(old, (int, float)) else UNSET_DEFAULTS.get(key)
            if base is None:
                continue
            new = {"add": base + amount, "mul": base * amount, "set": amount,
                   "min_set": max(base, amount)}[how]
            lo, hi = LIMITS.get(key, (float("-inf"), float("inf")))
            if "nozzle_temperature" in key:
                lo = max(lo, settings.get("nozzle_temperature_range_low") or lo)
                hi = min(hi, settings.get("nozzle_temperature_range_high") or hi,
                         (printer.get("nozzle_max_temp") or 999) - 5)
            if "plate_temp" in key and printer.get("bed_max_temp"):
                hi = min(hi, printer["bed_max_temp"])
            new = _round(key, min(max(new, lo), hi))
            if new == old:
                continue  # already at the limit, nothing to change
            out[key] = {"key": key, "old": old, "new": new, "why": why, "issue": iid}
            if key == "pressure_advance":
                out["enable_pressure_advance"] = {"key": "enable_pressure_advance",
                                                  "old": settings.get("enable_pressure_advance"), "new": 1,
                                                  "why": "Turns on pressure advance from this profile.",
                                                  "issue": iid, "companion": True}
        if sum(1 for c in out.values() if not c.get("companion")) >= MAX_CHANGES:
            break
    return list(out.values())


# ----------------------------------------------------------------- the AI
SYSTEM_PROMPT = (
    "You are an expert at diagnosing FDM 3D-printing problems from photos.\n"
    "Pick the problems you can actually see (or that the user describes) from the list you are given, "
    "using only those ids. Most photos show one main problem; list at most 3, most likely first.\n"
    "confidence is 0 to 1. In 'evidence' say briefly what in the photo or description shows it.\n"
    "If the photo is unclear or shows no problem, return an empty list and say so in summary.\n"
    "Don't suggest numeric setting values; the app does that."
)


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "issues": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string", "enum": list(ISSUES)},
                               "confidence": {"type": "number"}, "evidence": {"type": "string"}},
                "required": ["id", "confidence", "evidence"]}},
            "summary": {"type": "string"},
        },
        "required": ["issues", "summary"],
    }


def user_prompt(description: str, material: str | None, settings: dict) -> str:
    options = "\n".join(f"- {k}: {v['title']} — {v['looks_like']}" for k, v in ISSUES.items())
    keys = ("nozzle_temperature", "textured_plate_temp", "filament_flow_ratio", "pressure_advance",
            "filament_max_volumetric_speed", "filament_retraction_length", "fan_max_speed")
    current = ", ".join(f"{k}={settings[k]}" for k in keys if k in settings)
    return (f"Problems to choose from:\n{options}\n\n"
            f"Material: {material or 'unknown'}. Current settings: {current or 'unknown'}.\n"
            f"What the user says: \"{description.strip() or '(nothing)'}\"\n"
            "Look at the attached photo(s) and diagnose.")


def diagnose_with_ai(chat, description: str, images: list[bytes], material: str | None, settings: dict) -> dict:
    msg = {"role": "user", "content": user_prompt(description, material, settings)}
    if images:
        msg["images"] = [base64.b64encode(b).decode() for b in images]
    content = chat([{"role": "system", "content": SYSTEM_PROMPT}, msg], schema())
    try:
        raw = json.loads(content)
    except ValueError:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise RuntimeError("The AI didn't return a usable answer. Try again, or pick the problem yourself.")
        raw = json.loads(m.group(0))
    issues, seen = [], set()
    for it in raw.get("issues") or []:
        iid = it.get("id") if isinstance(it, dict) else None
        if iid not in ISSUES or iid in seen:
            continue  # ignore anything outside the list
        seen.add(iid)
        try:
            conf = min(max(float(it.get("confidence", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            conf = 0.5
        issues.append({"id": iid, "confidence": round(conf, 2), "evidence": str(it.get("evidence", ""))[:300]})
    issues.sort(key=lambda i: -i["confidence"])
    return {"issues": issues[:3], "summary": str(raw.get("summary", ""))[:600]}

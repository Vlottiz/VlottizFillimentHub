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
            ("filament_retraction_length", "add", 0.2, "Pulls the filament back further before travel moves.",
             {"floor": 0.6}),
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
            ("filament_retraction_length", "add", 0.1, "Slightly more retraction at the seam.", {"floor": 0.5}),
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
        "looks_like": "ripples or faint echoes that start right after a corner, hole or letter and fade out along the wall",
        "changes": [],
        "process": [("Outer wall acceleration", "2000–3000 mm/s²", "Gentler direction changes shake the toolhead less.",
                     "outer_wall_acceleration", 2500)],
        "tips": ["Make sure input shaping is calibrated on the printer.", "Check the belts are tight."],
    },
    "vfa": {
        "title": "VFA (fine vertical ripples)",
        "looks_like": "fine, evenly spaced vertical ripples or a corduroy / wavy texture across smooth walls, "
                      "most visible on curved shiny surfaces under side light",
        "changes": [],
        "process": [("Outer wall speed", "{vfa_speed}", "Prints walls at the speed your VFA test found smoothest.",
                     "outer_wall_speed", "{vfa_speed}")],
        "tips": ["Vertical fine artifacts come from the motors at certain speeds, not the filament.",
                 "Run the VFA calibration if you haven't: the smoothest band gives the outer wall speed to use."],
    },
    "rough_walls": {
        "title": "Rough / uneven walls",
        "looks_like": "lumpy, wavy or uneven outer walls with layers bulging in and out, while the top and bottom "
                      "surfaces look and feel fine",
        "changes": [
            ("nozzle_temperature", "add", 10, "Hotter plastic flows more evenly when walls print fast."),
            ("filament_max_volumetric_speed", "mul", 0.85, "Caps how fast walls can go so the hotend keeps up."),
        ],
        "process": [
            ("Outer wall speed", "60–80 mm/s", "Slow, steady outer walls hide uneven filament.", "outer_wall_speed", 70),
            ("Inner wall speed", "80–100 mm/s", "Keeps inner walls from starving the outer ones.", "inner_wall_speed", 90),
        ],
        "tips": ["Test: print a 20 mm cube with the trial. Smooth walls = it was speed/temperature.",
                 "Still lumpy at random heights? Measure the filament diameter at ~10 spots; big swings mean a poor spool.",
                 "Lumps repeating at the same spacing are the Z axis (check the lead screws), not the filament.",
                 "Dry the spool: damp filament extrudes unevenly."],
    },
    "z_banding": {
        "title": "Z-banding (horizontal bands)",
        "looks_like": "horizontal bands or ridges repeating at regular heights all the way up the print",
        "changes": [],
        "process": [],
        "tips": ["Usually mechanical: check the Z axis / lead screws are clean and not binding.",
                 "Uneven filament diameter or damp filament can also band: dry the spool.",
                 "Use a layer height that's a multiple of the Z step (0.2 mm is safe)."],
    },
    "layer_line": {
        "title": "One line or band at a single height",
        "looks_like": "a single visible line, ridge or change in shine at one height, e.g. where the deck or cabin starts",
        "changes": [("slow_down_layer_time", "add", 4, "Keeps small layers from printing much faster than big ones.")],
        "process": [],
        "tips": ["Speed or cooling changes at that height (small layers print faster). Mostly cosmetic.",
                 "Setting one fixed outer wall speed makes it even."],
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
    "slow_down_layer_time": (2, 20),
}

# Words in your description that point at a problem. What you say counts
# strongly: you can see the print, the model only sees a photo.
KEYWORDS = {
    "stringing": r"string|hair|web|wisp|cobweb|ooz",
    "rough_walls": r"(rough|lumpy|bumpy|uneven|wavy|messy|ugly).{0,20}wall|wall.{0,20}(rough|lumpy|bumpy|uneven|wavy|messy)",
    "vfa": r"\bvfa\b|wavy|wave|ripple|corduroy|vertical (line|stripe)|salmon",
    "ringing": r"ringing|ghost|echo",
    "z_banding": r"band|horizontal line|z.?wobble|layer lines? (stand out|visible)",
    "warping": r"warp|lift|curl(ing)? (up|corner)|corner.*(lift|up)",
    "poor_adhesion": r"(won'?t|doesn'?t|not) stick|came off|detach|adhesion",
    "sagging_overhangs": r"droop|sag|overhang|bridg",
    "blobs_zits": r"blob|zit|pimple|seam",
    "under_extrusion": r"gap|under.?extru|thin wall|missing|spongy|holes? in",
    "over_extrusion": r"over.?extru|too much|bulg(e|ing) wall|rough top",
    "layer_separation": r"split|crack|delaminat|weak layer|snap",
    "elephant_foot": r"elephant|bottom.*(wide|bulge|flare)",
    "bulging_corners": r"corner.*(bulge|blob|stick out)",
    "wet_filament": r"pop|hiss|crackl|bubbl|steam",
}
MIN_CONFIDENCE = 0.45

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
        for key, how, amount, why, *extra in ISSUES.get(iid, {}).get("changes", []):
            opts = extra[0] if extra else {}
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
            if "floor" in opts and new < opts["floor"]:
                new = opts["floor"]  # e.g. retraction that's almost zero jumps to a sensible value
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
            why_text = why if not ("floor" in opts and new == opts["floor"]) else \
                f"{why} It was almost zero, so this sets a normal starting value."
            out[key] = {"key": key, "old": old, "new": new, "why": why_text, "issue": iid}
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
    "Rules:\n"
    "- The user's own description is the most important evidence: they can see and touch the print.\n"
    "- Only report a problem you can clearly see in the photo or that the user describes. Don't guess.\n"
    "- Silk and shiny filaments reflect light: bright streaks, glare and colour shifts are NOT defects.\n"
    "- Don't confuse: fine vertical ripples on smooth walls = vfa (not ringing unless they start at a corner "
    "or feature and fade out); hairs across gaps = stringing; corners lifted off the BED = warping (a part held "
    "in a hand can't show warping unless its bottom is visibly bent); drooping undersides of overhangs = "
    "sagging_overhangs.\n"
    "- Usually there is one main problem; list at most 3, most likely first, using only the given ids.\n"
    "- confidence is 0 to 1. In 'evidence' say exactly what you see and where.\n"
    "- If nothing is clearly wrong, return an empty list and say so in summary.\n"
    "- Don't suggest numeric setting values; the app does that."
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
    return (f"What the user says about the print: \"{description.strip() or '(nothing)'}\"\n\n"
            f"Problems to choose from:\n{options}\n\n"
            f"Material: {material or 'unknown'}. Current settings: {current or 'unknown'}.\n"
            "Look at the attached photo(s) and diagnose, giving the user's description the most weight.")


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


def process_suggestions(issue_ids: list[str], context: dict | None = None) -> list[dict]:
    """Print (process) settings to change in Orca; the app can't change these itself.

    Wall ones carry `key` and `number`, so a trial profile can keep them in its Walls section.
    """
    context = context or {}
    out, seen = [], set()
    for iid in issue_ids:
        for setting, value, why, *rest in ISSUES.get(iid, {}).get("process", []):
            key, number = (rest + [None, None])[:2]
            if "{vfa_speed}" in value:
                v = context.get("vfa_speed")
                value = f"{int(round(v))} mm/s (your VFA test)" if v else \
                    "the speed of your VFA test's smoothest band (run the VFA calibration)"
                number = int(round(v)) if v else None
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            item = {"setting": setting, "value": value, "why": why, "issue": iid}
            if key and isinstance(number, (int, float)):
                item.update(key=key, number=number, current=(context.get("walls") or {}).get(key))
            out.append(item)
    return out


def keyword_issues(description: str) -> list[str]:
    text = (description or "").lower()
    return [iid for iid, pat in KEYWORDS.items() if re.search(pat, text)]


def combine(ai_issues: list[dict], description: str) -> list[dict]:
    """Merge the AI's guesses with what the user wrote.

    Problems the user names are boosted (or added); low-confidence guesses
    the user didn't mention are dropped. At most 3, most likely first.
    """
    said = keyword_issues(description)
    by_id = {i["id"]: dict(i) for i in ai_issues}
    for iid in said:
        if iid in by_id:
            by_id[iid]["confidence"] = round(min(1.0, by_id[iid]["confidence"] + 0.3), 2)
            by_id[iid]["evidence"] = (by_id[iid]["evidence"] + " (you mentioned it too)").strip()
        else:
            by_id[iid] = {"id": iid, "confidence": 0.6, "evidence": "From your description."}
    kept = [i for i in by_id.values() if i["confidence"] >= MIN_CONFIDENCE or i["id"] in said]
    kept.sort(key=lambda i: -i["confidence"])
    return kept[:3]

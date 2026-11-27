"""Turn OrcaSlicer calibration-print results into new filament settings.

Each test describes: how to start it in Orca, what the print looks like, how
to judge or measure it, exactly which number to type in, and how the new
value is calculated. The UI shows all of this.

Inputs accept any decimal (including negatives and comma decimals); ranges
are checked here with an explanation instead of by the browser.
"""

from __future__ import annotations

TESTS = {
    "temperature": {
        "title": "Temperature tower",
        "orca_menu": "Calibration → Temperature",
        "order": 1,
        "setting": "nozzle_temperature",
        "setup": [
            "Select this filament profile in Orca first.",
            "In the dialog set Start = the hottest end of the filament's range and End = the coolest "
            "(e.g. 230 → 190 for PLA). Orca prints one block per 5 °C, hottest at the bottom.",
        ],
        "what_you_get": "A tower of stacked blocks. Each block has its temperature printed on the side "
                        "and includes an overhang and a small bridge.",
        "measure": [
            "Look at each block in good light. Check the overhang (no curling or drooping), the bridge "
            "(no sagging strands), the surface (smooth, not glossy-blobby) and the gap between the two "
            "pillars (fewest strings).",
            "Try to snap the tower between blocks: if the cold blocks break easily, layer strength is too weak there.",
            "If two blocks look equally good, pick the cooler one; if you print fast, go one block hotter.",
        ],
        "enter": "The temperature printed on the best block.",
        "example": "Blocks go 230, 225, 220 … 190. The 215 block has the cleanest overhang and least stringing → enter 215.",
        "measure_height": False,
        "inputs": [{"key": "best_temp", "label": "Temperature on the best block (°C)", "placeholder": "e.g. 215"}],
    },
    "flow_pass1": {
        "title": "Flow rate, pass 1",
        "orca_menu": "Calibration → Flow rate → Pass 1",
        "order": 2,
        "setting": "filament_flow_ratio",
        "setup": [
            "Export this profile to Orca first, so the test starts from the flow ratio shown below.",
            "Orca opens a new project with the test plate; slice and print it as-is.",
        ],
        "what_you_get": "Nine square blocks with a number (the modifier) on each: -20, -15, -10, -5, 0, 5, 10, 15, 20.",
        "measure": [
            "Look at the top surfaces at a low angle under a light, and run a fingernail across them.",
            "Too little flow: you see gaps or lines between the top lines. Too much: ridges, a rough "
            "or bumpy feel, and the number on top looks blobby.",
            "Pick the smoothest top with no gaps. If it's between two blocks, pick the one with fewer gaps.",
        ],
        "enter": "The number on the best block, including its minus sign (e.g. -5).",
        "example": "Current flow 0.98, best block is -5 → new flow = 0.98 × (100 − 5) / 100 = 0.931.",
        "measure_height": False,
        "inputs": [{"key": "modifier", "label": "Number on the smoothest block", "placeholder": "e.g. -5"}],
    },
    "flow_pass2": {
        "title": "Flow rate, pass 2 (fine)",
        "orca_menu": "Calibration → Flow rate → Pass 2",
        "order": 3,
        "setting": "filament_flow_ratio",
        "setup": [
            "Save pass 1 here and export the profile to Orca first. Pass 2 must start from the pass 1 result.",
        ],
        "what_you_get": "Ten blocks numbered 0, -1, -2 … -9.",
        "measure": [
            "Judge the tops exactly like pass 1: smooth to the touch, no gaps between lines, no ridges.",
            "Differences are small here; a bright light from the side helps a lot.",
        ],
        "enter": "The number on the best block (0 to -9).",
        "example": "Current flow 0.931, best block is -3 → new flow = 0.931 × 97 / 100 = 0.903.",
        "measure_height": False,
        "inputs": [{"key": "modifier", "label": "Number on the smoothest block", "placeholder": "e.g. -3"}],
    },
    "flow_yolo": {
        "title": "Flow rate, YOLO",
        "orca_menu": "Calibration → Flow rate → YOLO (Recommended) or YOLO (perfectionist)",
        "order": 3,
        "alt": "Instead of passes 1 + 2",
        "setting": "filament_flow_ratio",
        "setup": ["One print instead of pass 1 + pass 2. Export this profile to Orca first."],
        "what_you_get": "Blocks labelled with small decimals, e.g. -0.05 … +0.05 (Recommended, 0.01 apart) "
                        "or -0.04 … +0.035 (perfectionist, 0.005 apart).",
        "measure": [
            "Pick the block with the smoothest top surface and no gaps, as in pass 1.",
        ],
        "enter": "The decimal printed on the best block, with its sign (e.g. -0.04 or 0.015).",
        "example": "Current flow 0.98, best block is -0.04 → new flow = 0.98 − 0.04 = 0.94.",
        "measure_height": False,
        "inputs": [{"key": "modifier", "label": "Decimal on the smoothest block", "placeholder": "e.g. -0.04"}],
    },
    "pressure_advance": {
        "title": "Pressure advance",
        "orca_menu": "Calibration → Pressure advance",
        "order": 4,
        "setting": "pressure_advance",
        "setup": [
            "Pick Pattern (easiest to read), Line, or Tower in the dialog. Defaults for a direct-drive "
            "toolhead like the U1: start 0, end 0.08, step 0.002 (pattern/line) or 0.002 per mm (tower).",
            "On a toolchanger, print it on the toolhead you'll normally use for this filament.",
            "Write down Start and Step; you need them for the Tower method.",
        ],
        "what_you_get": "Pattern: rows of nested V-shapes with PA values printed above them. "
                        "Line: rows of lines with a value at the end of each. Tower: a hollow square tower whose PA rises with height.",
        "measure": [
            "Pattern: find the V whose tip is sharpest — no bulge or blob at the corner and no gap or thin spot "
            "just after it. Read the value above it (if only every other V is labelled, it's halfway between the two labels).",
            "Line: find the line with the most even width along its whole length (no thick start, no thin end).",
            "Tower: with calipers, measure from the bed side (bottom of the print) up to the height where the "
            "corners look sharpest. Too low PA = bulging corners; too high = rounded or gappy corners.",
        ],
        "enter": "Pattern/Line: the PA value of the best one (e.g. 0.032). Tower: the height in mm, plus the start and step you used.",
        "example": "Pattern: the sharpest V is labelled 0.032 → enter 0.032. "
                   "Tower: start 0, step 0.002/mm, best corners at 16 mm → PA = 0 + 16 × 0.002 = 0.032.",
        "measure_height": True,
        "inputs": [
            {"key": "method", "label": "Which test did you print?", "type": "choice",
             "options": [["pattern", "Pattern or Line"], ["tower", "Tower"]], "default": "pattern"},
            {"key": "pa", "label": "PA value of the best V / line", "placeholder": "e.g. 0.032", "show_if": ["method", "pattern"]},
            {"key": "start", "label": "Start PA you used", "default": 0, "show_if": ["method", "tower"]},
            {"key": "step", "label": "Step per mm you used", "default": 0.002, "show_if": ["method", "tower"]},
            {"key": "height", "label": "Height of the best corners (mm)", "placeholder": "e.g. 16",
             "show_if": ["method", "tower"]},
        ],
    },
    "retraction": {
        "title": "Retraction",
        "orca_menu": "Calibration → Retraction test",
        "order": 5,
        "setting": "filament_retraction_length",
        "setup": [
            "Defaults are fine for direct drive: start 0, end 2, step 0.1 mm. Note Start and Step.",
            "The retraction length increases by Step for every 1 mm of tower height.",
        ],
        "what_you_get": "Two thin towers side by side. Stringing between them fades as retraction increases.",
        "measure": [
            "Find the lowest point where the strings between the towers stop (a few wisps are fine).",
            "With calipers, measure from the bed side (bottom of the print) up to that point.",
            "Don't go much higher than needed: too much retraction causes gaps and clogs.",
        ],
        "enter": "The height in mm where stringing stops, plus the start and step you used.",
        "example": "Start 0, step 0.1, strings stop at 8 mm → retraction = 0 + 8 × 0.1 = 0.8 mm.",
        "measure_height": True,
        "inputs": [
            {"key": "start", "label": "Start length you used (mm)", "default": 0},
            {"key": "step", "label": "Step per mm of height", "default": 0.1},
            {"key": "height", "label": "Height where stringing stops (mm)", "placeholder": "e.g. 8"},
        ],
    },
    "max_flow": {
        "title": "Max flowrate",
        "orca_menu": "Calibration → Max flowrate",
        "order": 6,
        "setting": "filament_max_volumetric_speed",
        "setup": [
            "Defaults: start 5, end 20, step 0.5 mm³/s per mm of height. For high-flow hotends raise End (e.g. 35).",
            "Note Start and Step; you need them below.",
        ],
        "what_you_get": "A thin wavy wall that prints faster (more flow) the higher it gets. The result sets the "
                        "filament's Max volumetric speed, which caps how fast Orca will print it.",
        "measure": [
            "Find the first height where the wall gets worse: gaps, rough or matte bands, missing lines, "
            "or a sudden change in shine. Under-extrusion starts here.",
            "With calipers, measure from the bed side up to that point. When unsure, take the lower height.",
        ],
        "enter": "The height in mm where it first goes wrong, plus the start and step you used. "
                 "A safety margin is taken off automatically (10% default).",
        "example": "Start 5, step 0.5, problems start at 30 mm → 5 + 30 × 0.5 = 20 mm³/s, minus 10% = 18 mm³/s.",
        "measure_height": True,
        "inputs": [
            {"key": "start", "label": "Start (mm³/s)", "default": 5},
            {"key": "step", "label": "Step (mm³/s per mm)", "default": 0.5},
            {"key": "height", "label": "Height where problems start (mm)", "placeholder": "e.g. 30"},
            {"key": "margin", "label": "Safety margin (%)", "default": 10},
        ],
    },
    "vfa": {
        "title": "VFA (vertical fine artifacts)",
        "orca_menu": "Calibration → VFA",
        "order": 7,
        "setting": None,
        "setup": ["Use the default speed range."],
        "what_you_get": "A tall tower printed at a different speed in each band.",
        "measure": [
            "Look at the walls under a side light for fine vertical ripples. Note the band with the smoothest walls "
            "and any bands that are clearly worse (those speeds hit a resonance).",
        ],
        "enter": "The speed of the smoothest band (read from the label, or work it out from start + band × step).",
        "example": "Smoothest band is the 120 mm/s one → enter 120. Logged only; it doesn't change the filament profile.",
        "measure_height": False,
        "inputs": [{"key": "best_speed", "label": "Speed of the smoothest band (mm/s)", "placeholder": "e.g. 120"}],
    },
}


def parse_number(v) -> float | None:
    """Accept 0.04, -0.04, .04, 0,04, '+5', ' 5 ' etc."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(",", ".").replace("−", "-")
    if s in ("", "-", "+", "."):
        return None
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"'{v}' isn't a number. Use digits and a dot or comma, e.g. -0.04")


def compute(test: str, inputs: dict, current: dict) -> dict:
    """Return {setting, old, new, explanation, extra} for a test result."""
    if test not in TESTS:
        raise ValueError(f"Unknown test: {test}")
    t = TESTS[test]
    setting = t["setting"]
    old = current.get(setting) if setting else None
    labels = {i["key"]: i["label"] for i in t["inputs"]}

    def num(key, default=None):
        v = parse_number(inputs.get(key))
        if v is None:
            if default is None:
                raise ValueError(f"Fill in: {labels.get(key, key)}")
            return float(default)
        return v

    extra: dict = {}
    if test == "temperature":
        new = int(round(num("best_temp")))
        if not 150 <= new <= 350:
            raise ValueError("That temperature looks wrong; enter the number on the block in °C.")
        lo = current.get("nozzle_temperature_range_low")
        hi = current.get("nozzle_temperature_range_high")
        first = new + 5 if not hi else min(new + 5, int(hi))
        extra["nozzle_temperature_initial_layer"] = first
        if lo and new < lo:
            extra["nozzle_temperature_range_low"] = new
        if hi and new > hi:
            extra["nozzle_temperature_range_high"] = new
        why = f"Nozzle {new} °C; first layer {first} °C."
    elif test in ("flow_pass1", "flow_pass2"):
        base = float(old or 1.0)
        mod = num("modifier")
        if -1 < mod < 1 and mod != 0:
            raise ValueError(f"{mod:g} looks like a YOLO value. Pass 1/2 blocks use whole numbers like -5; "
                             "for decimals like -0.04 choose 'Flow rate, YOLO'.")
        new = round(base * (100 + mod) / 100, 4)
        why = f"{base:g} × (100 {'+' if mod >= 0 else '−'} {abs(mod):g}) / 100 = {new}"
    elif test == "flow_yolo":
        base = float(old or 1.0)
        mod = num("modifier")
        if abs(mod) >= 1:
            raise ValueError(f"{mod:g} looks like a pass 1/2 number. YOLO blocks are small decimals like -0.04; "
                             "for whole numbers choose 'Flow rate, pass 1' or 'pass 2'.")
        new = round(base + mod, 4)
        why = f"{base:g} {'+' if mod >= 0 else '−'} {abs(mod):g} = {new}"
    elif test == "pressure_advance":
        if inputs.get("method") == "tower":
            start, step, height = num("start", 0), num("step", 0.002), num("height")
            new = round(start + height * step, 4)
            why = f"{start:g} + {height:g} mm × {step:g} = {new}"
        else:
            new = round(num("pa"), 4)
            why = f"Pressure advance {new}."
        if new < 0:
            raise ValueError("Pressure advance can't be negative in Klipper. If you're reading a flow test block "
                             "(like -0.04), use the 'Flow rate' step instead.")
        extra["enable_pressure_advance"] = 1
        why += " Enabled in the filament profile."
    elif test == "retraction":
        start, step, height = num("start", 0), num("step", 0.1), num("height")
        new = round(start + height * step, 2)
        why = f"{start:g} + {height:g} mm × {step:g} = {new} mm"
    elif test == "max_flow":
        start, step, height = num("start", 5), num("step", 0.5), num("height")
        margin = num("margin", 10)
        raw = start + height * step
        new = round(raw * (1 - margin / 100), 1)
        extra["_measured"] = round(raw, 2)
        why = f"{start:g} + {height:g} mm × {step:g} = {raw:.1f} mm³/s, minus {margin:g}% = {new}"
    elif test == "vfa":
        new = None
        why = f"Cleanest walls at {num('best_speed'):g} mm/s (logged only)."
    else:  # pragma: no cover
        raise ValueError(test)

    if setting and new is not None:
        if test.startswith("flow") and not 0.5 <= new <= 1.5:
            raise ValueError(f"That gives a flow ratio of {new}, which can't be right; check the number you entered.")
        if test == "pressure_advance" and new > 1.0:
            raise ValueError("Pressure advance over 1 isn't realistic on a direct-drive printer; check the value.")
        if test == "retraction" and not 0 <= new <= 10:
            raise ValueError(f"Retraction of {new} mm doesn't look right; check the height, start and step.")
    return {"setting": setting, "old": old, "new": new, "explanation": why, "extra": extra}


def tests_for_ui() -> list[dict]:
    return [{"id": k, **v} for k, v in sorted(TESTS.items(), key=lambda kv: kv[1]["order"])]

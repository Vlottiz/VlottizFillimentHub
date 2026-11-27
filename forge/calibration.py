"""Turn OrcaSlicer calibration-print results into new filament settings.

Each test describes how to run it in Orca (menu Calibration -> ...), what to
read off the print, and how to compute the new value.
"""

from __future__ import annotations

TESTS = {
    "temperature": {
        "title": "Temperature tower",
        "orca_menu": "Calibration → Temperature",
        "order": 1,
        "setting": "nozzle_temperature",
        "inputs": [{"key": "best_temp", "label": "Best-looking block temperature (°C)", "type": "number", "step": 5}],
        "howto": [
            "Set the tower's start/end to the filament's temperature range.",
            "Print it, then look for the block with the cleanest bridges, overhangs and least stringing.",
            "If two blocks look the same, choose the lower temperature.",
        ],
    },
    "flow_pass1": {
        "title": "Flow rate, pass 1",
        "orca_menu": "Calibration → Flow rate → Pass 1",
        "order": 2,
        "setting": "filament_flow_ratio",
        "inputs": [{"key": "modifier", "label": "Smoothest block's number (e.g. -5, 0, +10)", "type": "number", "step": 1}],
        "howto": [
            "Make sure the profile's current flow ratio is what Orca uses for the test.",
            "Feel the top surfaces; pick the smoothest one with no gaps.",
            "Enter the number printed on it.",
        ],
    },
    "flow_pass2": {
        "title": "Flow rate, pass 2 (fine)",
        "orca_menu": "Calibration → Flow rate → Pass 2",
        "order": 3,
        "setting": "filament_flow_ratio",
        "inputs": [{"key": "modifier", "label": "Smoothest block's number (e.g. -3)", "type": "number", "step": 1}],
        "howto": [
            "Export the pass 1 result to Orca first so pass 2 starts from it.",
            "Pick the smoothest block and enter its number.",
        ],
    },
    "flow_yolo": {
        "title": "Flow rate, YOLO",
        "orca_menu": "Calibration → Flow rate → YOLO",
        "order": 3,
        "setting": "filament_flow_ratio",
        "inputs": [{"key": "modifier", "label": "Smoothest block's number (e.g. -0.02)", "type": "number", "step": 0.01}],
        "howto": ["Single-pass alternative to pass 1 + pass 2. Enter the decimal shown on the best block."],
    },
    "pressure_advance": {
        "title": "Pressure advance",
        "orca_menu": "Calibration → Pressure advance",
        "order": 4,
        "setting": "pressure_advance",
        "inputs": [{"key": "pa", "label": "Best pressure advance value", "type": "number", "step": 0.001}],
        "howto": [
            "The pattern or tower test both work on Klipper.",
            "Pick the line or corner with the sharpest corners and even width, then read its PA value.",
            "On a toolchanger, calibrate on the toolhead you will normally load this filament in.",
        ],
    },
    "retraction": {
        "title": "Retraction",
        "orca_menu": "Calibration → Retraction test",
        "order": 5,
        "setting": "filament_retraction_length",
        "inputs": [
            {"key": "start", "label": "Start length (mm)", "type": "number", "step": 0.1, "default": 0},
            {"key": "step", "label": "Step per mm of height", "type": "number", "step": 0.01, "default": 0.1},
            {"key": "height", "label": "Height where stringing stops (mm)", "type": "number", "step": 0.5},
        ],
        "howto": [
            "Find the lowest height where stringing disappears.",
            "Length = start + height × step. Fill in the three values, or put the length in 'height' with step = 1 and start = 0.",
        ],
    },
    "max_flow": {
        "title": "Max volumetric speed",
        "orca_menu": "Calibration → Max flowrate",
        "order": 6,
        "setting": "filament_max_volumetric_speed",
        "inputs": [
            {"key": "start", "label": "Start (mm³/s)", "type": "number", "step": 0.5, "default": 5},
            {"key": "step", "label": "Step (mm³/s per mm height)", "type": "number", "step": 0.05, "default": 0.5},
            {"key": "height", "label": "Height where the print first fails (mm)", "type": "number", "step": 0.5},
            {"key": "margin", "label": "Safety margin (%)", "type": "number", "step": 1, "default": 10},
        ],
        "howto": [
            "Measure the height where the walls first get gaps or rough.",
            "Value = start + height × step, then the safety margin is taken off.",
            "Also update the printer's 'hotend max flow' if this is the highest you have seen.",
        ],
    },
    "vfa": {
        "title": "VFA (vertical fine artifacts)",
        "orca_menu": "Calibration → VFA",
        "order": 7,
        "setting": None,
        "inputs": [{"key": "best_speed", "label": "Speed with the cleanest walls (mm/s)", "type": "number", "step": 10}],
        "howto": ["Doesn't change the filament profile; it's logged so you know which speeds to avoid."],
    },
}


def compute(test: str, inputs: dict, current: dict) -> dict:
    """Return {setting, old, new, explanation, extra} for a test result."""
    if test not in TESTS:
        raise ValueError(f"Unknown test: {test}")
    t = TESTS[test]
    setting = t["setting"]
    old = current.get(setting) if setting else None

    def num(key, default=None):
        v = inputs.get(key, default)
        if v is None or v == "":
            if default is None:
                raise ValueError(f"Missing value: {key}")
            return float(default)
        return float(v)

    extra: dict = {}
    if test == "temperature":
        new = int(round(num("best_temp")))
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
        new = round(base * (100 + mod) / 100, 4)
        why = f"{base} × (100 {'+' if mod >= 0 else '−'} {abs(mod):g}) / 100 = {new}"
    elif test == "flow_yolo":
        base = float(old or 1.0)
        mod = num("modifier")
        new = round(base + mod, 4)
        why = f"{base} {'+' if mod >= 0 else '−'} {abs(mod):g} = {new}"
    elif test == "pressure_advance":
        new = round(num("pa"), 4)
        extra["enable_pressure_advance"] = 1
        why = f"Pressure advance {new} (enabled in the filament profile)."
    elif test == "retraction":
        start, step, height = num("start", 0), num("step", 0.1), num("height")
        new = round(start + height * step, 2)
        why = f"{start:g} + {height:g} × {step:g} = {new} mm"
    elif test == "max_flow":
        start, step, height = num("start", 5), num("step", 0.5), num("height")
        margin = num("margin", 10)
        raw = start + height * step
        new = round(raw * (1 - margin / 100), 1)
        extra["_measured"] = round(raw, 2)
        why = f"{start:g} + {height:g} × {step:g} = {raw:.1f} mm³/s, minus {margin:g}% = {new}"
    elif test == "vfa":
        new = None
        why = f"Cleanest walls at {num('best_speed'):g} mm/s (logged only)."
    else:  # pragma: no cover
        raise ValueError(test)

    if setting and new is not None:
        if test.startswith("flow") and not 0.5 <= new <= 1.5:
            raise ValueError(f"Flow ratio {new} looks wrong; check the number you entered.")
        if test == "pressure_advance" and not 0 <= new <= 1.0:
            raise ValueError("Pressure advance should be between 0 and 1 on Klipper.")
    return {"setting": setting, "old": old, "new": new, "explanation": why, "extra": extra}


def tests_for_ui() -> list[dict]:
    return [{"id": k, **v} for k, v in sorted(TESTS.items(), key=lambda kv: kv[1]["order"])]

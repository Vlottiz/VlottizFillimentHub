"""Build a filament profile by layering sources, and score how sure we are.

Layers, lowest to highest priority:
    baseline      generic values for the material family
    manufacturer  numbers from the spool / store page
    printer       clamps from your printer's limits (printer.cfg)
    manual        values you typed in yourself
    calibrated    values from calibration prints

Every setting remembers which layer it came from. Confidence and the
"± range" come from that.
"""

from __future__ import annotations

from . import materials

SOURCE_CONFIDENCE = {
    "calibrated": 95,
    "manual": 80,
    "printer": 75,
    "manufacturer": 70,
    "baseline": 40,
}

# How far off a value from each source could reasonably be (fraction).
SOURCE_TOLERANCE = {
    "calibrated": 0.02,
    "manual": 0.05,
    "printer": 0.08,
    "manufacturer": 0.08,
    "baseline": 0.15,
}

# Settings that matter most for print quality get more weight in the score.
KEY_WEIGHTS = {
    "nozzle_temperature": 3,
    "filament_flow_ratio": 3,
    "pressure_advance": 2,
    "filament_max_volumetric_speed": 2,
    "hot_plate_temp": 1,
    "textured_plate_temp": 1,
    "fan_max_speed": 1,
    "filament_retraction_length": 1,
}

SPEC_FIELDS = (
    "nozzle_min", "nozzle_max", "bed_min", "bed_max",
    "density", "diameter", "max_volumetric_speed",
)


def _round5(x: float) -> int:
    return int(5 * round(float(x) / 5))


def _spec_layer(spec: dict) -> dict:
    """Turn manufacturer spec numbers into setting values."""
    out: dict = {}
    lo, hi = spec.get("nozzle_min"), spec.get("nozzle_max")
    if lo and hi:
        # Slightly below the middle of the range is the usual sweet spot for
        # fast-ish printing; first layer a little hotter.
        mid = lo + (hi - lo) * 0.45
        out["nozzle_temperature"] = _round5(mid)
        out["nozzle_temperature_initial_layer"] = min(int(hi), _round5(mid) + 5)
        out["nozzle_temperature_range_low"] = int(lo)
        out["nozzle_temperature_range_high"] = int(hi)
    elif lo or hi:
        t = lo or hi
        out["nozzle_temperature"] = _round5(t)
        out["nozzle_temperature_initial_layer"] = _round5(t)
    blo, bhi = spec.get("bed_min"), spec.get("bed_max")
    if blo or bhi:
        bed = _round5(((blo or bhi) + (bhi or blo)) / 2)
        for k in ("hot_plate_temp", "hot_plate_temp_initial_layer",
                  "textured_plate_temp", "textured_plate_temp_initial_layer"):
            out[k] = bed
    if spec.get("density"):
        out["filament_density"] = float(spec["density"])
    if spec.get("diameter"):
        out["filament_diameter"] = float(spec["diameter"])
    if spec.get("max_volumetric_speed"):
        out["filament_max_volumetric_speed"] = float(spec["max_volumetric_speed"])
    return out


def _printer_layer(settings: dict, printer: dict | None, notes: list[str]) -> dict:
    """Clamp settings to what the printer can physically do."""
    if not printer:
        return {}
    out: dict = {}
    nmax = printer.get("nozzle_max_temp")
    if nmax:
        for k in ("nozzle_temperature", "nozzle_temperature_initial_layer"):
            if settings.get(k) and settings[k] > nmax - 5:
                out[k] = int(nmax - 5)
                notes.append(f"{k} capped to {int(nmax - 5)} °C (hotend max {int(nmax)} °C).")
    bmax = printer.get("bed_max_temp")
    if bmax:
        for k in ("hot_plate_temp", "hot_plate_temp_initial_layer",
                  "textured_plate_temp", "textured_plate_temp_initial_layer"):
            if settings.get(k) and settings[k] > bmax:
                out[k] = int(bmax)
        if any(k.endswith("plate_temp") for k in out):
            notes.append(f"Bed temperatures capped to {int(bmax)} °C (bed max).")
    flow = printer.get("hotend_max_flow")
    mvs = settings.get("filament_max_volumetric_speed")
    if flow and mvs and mvs > flow:
        out["filament_max_volumetric_speed"] = float(flow)
        notes.append(f"Max volumetric speed capped to {flow} mm³/s (hotend limit).")
    return out


def speed_hint(settings: dict, printer: dict | None,
               layer_height: float = 0.2, line_width: float | None = None) -> dict:
    """Fastest useful print speed for this filament on this printer."""
    nozzle = (printer or {}).get("nozzle_diameter") or 0.4
    lw = line_width or round(nozzle * 1.05, 2)
    mvs = settings.get("filament_max_volumetric_speed") or 0
    flow_speed = mvs / (layer_height * lw) if mvs else None
    machine = (printer or {}).get("max_velocity")
    limit = min(x for x in (flow_speed, machine) if x) if (flow_speed or machine) else None
    return {
        "layer_height": layer_height,
        "line_width": lw,
        "flow_limited_speed": round(flow_speed) if flow_speed else None,
        "machine_max_speed": machine,
        "effective_max_speed": round(limit) if limit else None,
        "limited_by": ("filament flow" if flow_speed and (not machine or flow_speed <= machine)
                       else "printer" if machine else None),
    }


def build(material: str, spec: dict | None = None, printer: dict | None = None,
          vendor: str | None = None, colour: str | None = None,
          cost_per_kg: float | None = None) -> dict:
    """Generate settings + per-setting sources for a new profile."""
    notes: list[str] = []
    settings = materials.baseline(material)
    sources = {k: "baseline" for k in settings}

    for k, v in _spec_layer(spec or {}).items():
        settings[k] = v
        sources[k] = "manufacturer"

    if vendor:
        settings["filament_vendor"] = vendor
        sources["filament_vendor"] = "manual"
    if colour:
        settings["default_filament_colour"] = colour
        sources["default_filament_colour"] = "manual"
    if cost_per_kg:
        settings["filament_cost"] = float(cost_per_kg)
        sources["filament_cost"] = "manual"

    for k, v in _printer_layer(settings, printer, notes).items():
        settings[k] = v
        sources[k] = "printer"

    info = materials.material_info(material)
    for label, text in info.items():
        notes.append(f"{label.capitalize()}: {text}")
    return {"settings": settings, "sources": sources, "notes": notes}


def _nice(x: float, ref: float):
    """Round a range bound to a sensible precision for its size."""
    if abs(ref) >= 20:
        return int(round(x))
    if abs(ref) >= 2:
        return round(x, 1)
    return round(x, 3)


def confidence(settings: dict, sources: dict) -> dict:
    """Overall confidence (0-100) and a ± range for each key setting."""
    total = weight_sum = 0.0
    per_key = {}
    for key, w in KEY_WEIGHTS.items():
        if key not in settings:
            continue
        src = sources.get(key, "baseline")
        # Pressure advance is only meaningful once it's been calibrated or set.
        if key == "pressure_advance" and src == "baseline":
            src_conf = 20
        else:
            src_conf = SOURCE_CONFIDENCE.get(src, 40)
        total += src_conf * w
        weight_sum += w
        val = settings[key]
        tol = SOURCE_TOLERANCE.get(src, 0.15)
        try:
            v = float(val)
            per_key[key] = {
                "value": val, "source": src, "confidence": src_conf,
                "low": _nice(v * (1 - tol), v), "high": _nice(v * (1 + tol), v),
                "tolerance_pct": round(tol * 100),
            }
        except (TypeError, ValueError):
            per_key[key] = {"value": val, "source": src, "confidence": src_conf}
    overall = round(total / weight_sum) if weight_sum else 0
    return {"overall": overall, "settings": per_key}

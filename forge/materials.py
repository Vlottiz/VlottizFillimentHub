"""Starting-point settings for each material family.

These are deliberately conservative, generic values. They are only the
lowest-confidence layer: manufacturer specs, your printer's limits and your
own calibration results all override them.

Keys match OrcaSlicer filament preset keys so the export is a straight copy.
"""

from __future__ import annotations

# Values every material shares unless overridden.
_COMMON = {
    "filament_diameter": 1.75,
    "filament_flow_ratio": 0.98,
    "enable_pressure_advance": 0,
    "pressure_advance": 0.04,
    "fan_cooling_layer_time": 60,
    "slow_down_layer_time": 6,
    "close_fan_the_first_x_layers": 1,
}

# name -> (settings, info)
MATERIALS: dict[str, dict] = {
    "PLA": {
        "settings": {
            "filament_type": "PLA", "filament_density": 1.24,
            "nozzle_temperature": 215, "nozzle_temperature_initial_layer": 220,
            "nozzle_temperature_range_low": 190, "nozzle_temperature_range_high": 230,
            "hot_plate_temp": 60, "hot_plate_temp_initial_layer": 60,
            "textured_plate_temp": 60, "textured_plate_temp_initial_layer": 60,
            "filament_max_volumetric_speed": 18,
            "fan_min_speed": 100, "fan_max_speed": 100, "overhang_fan_speed": 100,
            "slow_down_layer_time": 4, "temperature_vitrification": 55,
        },
        "info": {"dry": "45-55 °C for 4-6 h", "enclosure": "Not needed; open door if hot"},
    },
    "PLA+": {
        "settings": {
            "filament_type": "PLA", "filament_density": 1.23,
            "nozzle_temperature": 220, "nozzle_temperature_initial_layer": 225,
            "nozzle_temperature_range_low": 200, "nozzle_temperature_range_high": 235,
            "hot_plate_temp": 60, "hot_plate_temp_initial_layer": 60,
            "textured_plate_temp": 60, "textured_plate_temp_initial_layer": 60,
            "filament_max_volumetric_speed": 16,
            "fan_min_speed": 100, "fan_max_speed": 100, "overhang_fan_speed": 100,
            "slow_down_layer_time": 4, "temperature_vitrification": 55,
        },
        "info": {"dry": "45-55 °C for 4-6 h", "enclosure": "Not needed"},
    },
    "PLA Silk": {
        "settings": {
            "filament_type": "PLA", "filament_density": 1.24,
            "nozzle_temperature": 220, "nozzle_temperature_initial_layer": 225,
            "nozzle_temperature_range_low": 200, "nozzle_temperature_range_high": 235,
            "hot_plate_temp": 60, "hot_plate_temp_initial_layer": 60,
            "textured_plate_temp": 60, "textured_plate_temp_initial_layer": 60,
            "filament_max_volumetric_speed": 10,
            "fan_min_speed": 100, "fan_max_speed": 100, "overhang_fan_speed": 100,
            "slow_down_layer_time": 6, "temperature_vitrification": 55,
        },
        "info": {"dry": "45-55 °C for 4-6 h", "enclosure": "Not needed; prints slower for shine"},
    },
    "PLA-CF": {
        "settings": {
            "filament_type": "PLA-CF", "filament_density": 1.29,
            "nozzle_temperature": 220, "nozzle_temperature_initial_layer": 225,
            "nozzle_temperature_range_low": 200, "nozzle_temperature_range_high": 240,
            "hot_plate_temp": 60, "hot_plate_temp_initial_layer": 60,
            "textured_plate_temp": 60, "textured_plate_temp_initial_layer": 60,
            "filament_max_volumetric_speed": 12,
            "fan_min_speed": 80, "fan_max_speed": 100, "overhang_fan_speed": 100,
            "temperature_vitrification": 55,
        },
        "info": {"dry": "50-55 °C for 6 h", "enclosure": "Not needed", "nozzle": "Hardened nozzle required"},
    },
    "PETG": {
        "settings": {
            "filament_type": "PETG", "filament_density": 1.27, "filament_flow_ratio": 0.95,
            "nozzle_temperature": 240, "nozzle_temperature_initial_layer": 245,
            "nozzle_temperature_range_low": 220, "nozzle_temperature_range_high": 260,
            "hot_plate_temp": 75, "hot_plate_temp_initial_layer": 80,
            "textured_plate_temp": 75, "textured_plate_temp_initial_layer": 80,
            "filament_max_volumetric_speed": 12,
            "fan_min_speed": 20, "fan_max_speed": 50, "overhang_fan_speed": 90,
            "close_fan_the_first_x_layers": 3, "temperature_vitrification": 70,
        },
        "info": {"dry": "60-65 °C for 6-8 h", "enclosure": "Optional", "note": "Use a release agent on smooth PEI"},
    },
    "PETG-CF": {
        "settings": {
            "filament_type": "PETG-CF", "filament_density": 1.30, "filament_flow_ratio": 0.95,
            "nozzle_temperature": 250, "nozzle_temperature_initial_layer": 255,
            "nozzle_temperature_range_low": 230, "nozzle_temperature_range_high": 270,
            "hot_plate_temp": 75, "hot_plate_temp_initial_layer": 80,
            "textured_plate_temp": 75, "textured_plate_temp_initial_layer": 80,
            "filament_max_volumetric_speed": 10,
            "fan_min_speed": 20, "fan_max_speed": 50, "overhang_fan_speed": 90,
            "close_fan_the_first_x_layers": 3, "temperature_vitrification": 70,
        },
        "info": {"dry": "65 °C for 8 h", "enclosure": "Optional", "nozzle": "Hardened nozzle required"},
    },
    "ABS": {
        "settings": {
            "filament_type": "ABS", "filament_density": 1.04, "filament_flow_ratio": 0.95,
            "nozzle_temperature": 255, "nozzle_temperature_initial_layer": 260,
            "nozzle_temperature_range_low": 240, "nozzle_temperature_range_high": 270,
            "hot_plate_temp": 100, "hot_plate_temp_initial_layer": 100,
            "textured_plate_temp": 100, "textured_plate_temp_initial_layer": 100,
            "filament_max_volumetric_speed": 16,
            "fan_min_speed": 10, "fan_max_speed": 30, "overhang_fan_speed": 80,
            "close_fan_the_first_x_layers": 3, "temperature_vitrification": 100,
        },
        "info": {"dry": "70-80 °C for 4-6 h", "enclosure": "Required; ventilate fumes"},
    },
    "ASA": {
        "settings": {
            "filament_type": "ASA", "filament_density": 1.07, "filament_flow_ratio": 0.95,
            "nozzle_temperature": 260, "nozzle_temperature_initial_layer": 265,
            "nozzle_temperature_range_low": 240, "nozzle_temperature_range_high": 280,
            "hot_plate_temp": 100, "hot_plate_temp_initial_layer": 100,
            "textured_plate_temp": 100, "textured_plate_temp_initial_layer": 100,
            "filament_max_volumetric_speed": 14,
            "fan_min_speed": 10, "fan_max_speed": 30, "overhang_fan_speed": 80,
            "close_fan_the_first_x_layers": 3, "temperature_vitrification": 100,
        },
        "info": {"dry": "70-80 °C for 4-6 h", "enclosure": "Required; ventilate fumes"},
    },
    "TPU": {
        "settings": {
            "filament_type": "TPU", "filament_density": 1.21,
            "nozzle_temperature": 225, "nozzle_temperature_initial_layer": 225,
            "nozzle_temperature_range_low": 210, "nozzle_temperature_range_high": 240,
            "hot_plate_temp": 45, "hot_plate_temp_initial_layer": 45,
            "textured_plate_temp": 45, "textured_plate_temp_initial_layer": 45,
            "filament_max_volumetric_speed": 3.5,
            "fan_min_speed": 100, "fan_max_speed": 100, "overhang_fan_speed": 100,
            "temperature_vitrification": 30,
        },
        "info": {"dry": "50-55 °C for 6-8 h", "enclosure": "Not needed", "note": "Values are for 95A; softer TPU needs slower"},
    },
    "PA": {
        "settings": {
            "filament_type": "PA", "filament_density": 1.10, "filament_flow_ratio": 0.96,
            "nozzle_temperature": 275, "nozzle_temperature_initial_layer": 280,
            "nozzle_temperature_range_low": 260, "nozzle_temperature_range_high": 300,
            "hot_plate_temp": 90, "hot_plate_temp_initial_layer": 90,
            "textured_plate_temp": 90, "textured_plate_temp_initial_layer": 90,
            "filament_max_volumetric_speed": 10,
            "fan_min_speed": 0, "fan_max_speed": 30, "overhang_fan_speed": 60,
            "close_fan_the_first_x_layers": 3, "temperature_vitrification": 110,
        },
        "info": {"dry": "80-90 °C for 8-12 h (must be dry)", "enclosure": "Required"},
    },
    "PA-CF": {
        "settings": {
            "filament_type": "PA-CF", "filament_density": 1.20, "filament_flow_ratio": 0.96,
            "nozzle_temperature": 285, "nozzle_temperature_initial_layer": 290,
            "nozzle_temperature_range_low": 270, "nozzle_temperature_range_high": 300,
            "hot_plate_temp": 90, "hot_plate_temp_initial_layer": 90,
            "textured_plate_temp": 90, "textured_plate_temp_initial_layer": 90,
            "filament_max_volumetric_speed": 8,
            "fan_min_speed": 0, "fan_max_speed": 30, "overhang_fan_speed": 60,
            "close_fan_the_first_x_layers": 3, "temperature_vitrification": 120,
        },
        "info": {"dry": "80-90 °C for 8-12 h (must be dry)", "enclosure": "Required", "nozzle": "Hardened nozzle required"},
    },
    "PC": {
        "settings": {
            "filament_type": "PC", "filament_density": 1.20, "filament_flow_ratio": 0.95,
            "nozzle_temperature": 270, "nozzle_temperature_initial_layer": 275,
            "nozzle_temperature_range_low": 260, "nozzle_temperature_range_high": 290,
            "hot_plate_temp": 105, "hot_plate_temp_initial_layer": 105,
            "textured_plate_temp": 105, "textured_plate_temp_initial_layer": 105,
            "filament_max_volumetric_speed": 10,
            "fan_min_speed": 0, "fan_max_speed": 20, "overhang_fan_speed": 50,
            "close_fan_the_first_x_layers": 3, "temperature_vitrification": 140,
        },
        "info": {"dry": "80 °C for 8 h", "enclosure": "Required, heated if possible"},
    },
}


def material_names() -> list[str]:
    return list(MATERIALS)


def baseline(material: str) -> dict:
    """Return the full baseline settings for a material (common + specific)."""
    if material not in MATERIALS:
        raise KeyError(f"Unknown material: {material}")
    out = dict(_COMMON)
    out.update(MATERIALS[material]["settings"])
    return out


def material_info(material: str) -> dict:
    return dict(MATERIALS.get(material, {}).get("info", {}))

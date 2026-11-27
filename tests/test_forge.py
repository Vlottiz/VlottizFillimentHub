import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from forge import calibration, klipper, orca, tuning

KLIPPER_CFG = """
[include macros.cfg]
[printer]
kinematics: corexy
max_velocity: 500   # mm/s
max_accel = 20000
square_corner_velocity: 8.0

[stepper_x]
position_max: 270

[extruder]
nozzle_diameter: 0.400
max_temp: 300
max_extrude_cross_section: 5
pressure_advance: 0.02

[extruder1]
nozzle_diameter: 0.4
max_temp: 290

[heater_bed]
max_temp: 110

[gcode_macro PRINT_START]
gcode:
    G28
    ; comment
    M117 hi

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#
#*# [extruder]
#*# pressure_advance = 0.035
"""


def test_klipper_parse_and_save_config_override():
    s = klipper.summarize([KLIPPER_CFG])
    assert s["max_velocity"] == 500
    assert s["max_accel"] == 20000
    assert s["bed_max_temp"] == 110
    assert [e["name"] for e in s["extruders"]] == ["extruder", "extruder1"]
    assert s["extruders"][0]["pressure_advance"] == 0.035  # SAVE_CONFIG wins
    f = klipper.to_printer_fields(s)
    assert f["toolheads"] == 2
    assert f["nozzle_max_temp"] == 290  # lowest of the toolheads
    assert "gcode_macro print_start" in s["sections"]


def test_build_layers_sources_and_printer_clamp():
    printer = {"nozzle_max_temp": 260, "bed_max_temp": 90, "hotend_max_flow": 10, "max_velocity": 500}
    out = tuning.build("ABS", spec={"nozzle_min": 250, "nozzle_max": 270}, printer=printer)
    s, src = out["settings"], out["sources"]
    assert s["nozzle_temperature"] == 255 and src["nozzle_temperature"] == "printer"
    assert s["hot_plate_temp"] == 90
    assert s["filament_max_volumetric_speed"] == 10
    assert src["filament_density"] == "baseline"
    assert any("capped" in n for n in out["notes"])


def test_confidence_rises_with_calibration():
    out = tuning.build("PLA")
    before = tuning.confidence(out["settings"], out["sources"])["overall"]
    src = dict(out["sources"], nozzle_temperature="calibrated",
               filament_flow_ratio="calibrated", pressure_advance="calibrated")
    after = tuning.confidence(out["settings"], src)["overall"]
    assert after > before + 30


def test_speed_hint():
    h = tuning.speed_hint({"filament_max_volumetric_speed": 21}, {"nozzle_diameter": 0.4, "max_velocity": 500})
    assert h["limited_by"] == "filament flow"
    assert h["effective_max_speed"] == 250  # 21 / (0.2 * 0.42)


@pytest.mark.parametrize("test,inputs,current,expected", [
    ("flow_pass1", {"modifier": -5}, {"filament_flow_ratio": 0.98}, 0.931),
    ("flow_pass2", {"modifier": 3}, {"filament_flow_ratio": 0.931}, 0.9589),
    ("flow_yolo", {"modifier": -0.02}, {"filament_flow_ratio": 1.0}, 0.98),
    ("pressure_advance", {"pa": 0.032}, {}, 0.032),
    ("retraction", {"start": 0, "step": 0.1, "height": 8}, {}, 0.8),
    ("max_flow", {"start": 5, "step": 0.5, "height": 30, "margin": 10}, {}, 18.0),
    ("temperature", {"best_temp": 215}, {"nozzle_temperature_range_high": 230}, 215),
])
def test_calibration_math(test, inputs, current, expected):
    assert calibration.compute(test, inputs, current)["new"] == pytest.approx(expected)


def test_calibration_rejects_nonsense():
    with pytest.raises(ValueError):
        calibration.compute("flow_pass1", {"modifier": 80}, {"filament_flow_ratio": 1})


def test_orca_round_trip():
    preset = orca.to_preset("My PLA", {"nozzle_temperature": 215, "filament_flow_ratio": 0.9612,
                                      "filament_vendor": "Acme"}, material="PLA")
    assert preset["inherits"] == "Generic PLA @System"
    assert preset["nozzle_temperature"] == ["215"]
    assert preset["filament_flow_ratio"] == ["0.9612"]
    assert preset["from"] == "User" and preset["instantiation"] == "true"
    back = orca.from_preset(preset)
    assert back["settings"]["nozzle_temperature"] == 215
    assert back["settings"]["filament_vendor"] == "Acme"


def test_orca_scan_and_install(tmp_path):
    fil = tmp_path / "user" / "default" / "filament"
    fil.mkdir(parents=True)
    assert orca.user_filament_dirs(tmp_path) == [fil]
    path = orca.install(orca.to_preset("Test: PETG/1", {"nozzle_temperature": 240}), fil)
    assert path.name == "Test_ PETG_1.json"
    assert (fil / "Test_ PETG_1.info").exists()
    assert orca.scan(tmp_path)[0]["name"] == "Test: PETG/1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCA_CONFIG_DIR", str(tmp_path / "orca"))
    (tmp_path / "orca" / "user" / "default" / "filament").mkdir(parents=True)
    from forge import app as app_mod
    monkeypatch.setattr(app_mod, "PHOTO_DIR", tmp_path / "photos")
    monkeypatch.setattr(app_mod, "DATA_DIR", tmp_path)
    return TestClient(app_mod.create_app(tmp_path / "t.db"))


def test_full_flow(client, tmp_path):
    printers = client.get("/api/printers").json()
    assert printers[0]["name"].startswith("Snapmaker U1")
    pr_id = printers[0]["id"]

    # import printer.cfg into the U1
    r = client.post("/api/klipper/parse", files=[("files", ("printer.cfg", KLIPPER_CFG.encode()))])
    fields = r.json()["fields"]
    data = {**printers[0]["data"], **fields}
    client.put(f"/api/printers/{pr_id}", json={"name": printers[0]["name"], "data": data})

    f = client.post("/api/filaments", json={
        "brand": "Acme", "name": "PLA Matte", "material": "PLA", "colour_name": "Black",
        "colour_hex": "#111111", "weight_g": 1000, "price": 19.99,
        "spec": {"nozzle_min": 200, "nozzle_max": 230, "bed_min": 50, "bed_max": 65},
    }).json()
    p = client.post("/api/profiles", json={"filament_id": f["id"], "printer_id": pr_id}).json()
    assert p["name"] == "Acme PLA Matte Black @Snapmaker U1"
    assert p["settings"]["nozzle_temperature"] == 215
    assert p["settings"]["filament_cost"] == 19.99
    start_conf = p["confidence"]["overall"]

    # edit -> becomes manual
    p = client.put(f"/api/profiles/{p['id']}", json={"settings": {"fan_max_speed": 80}}).json()
    assert p["sources"]["fan_max_speed"] == "manual"

    # calibration with a photo
    r = client.post(f"/api/profiles/{p['id']}/calibrations", data={
        "test": "flow_pass1", "inputs": json.dumps({"modifier": -5}), "notes": "top looked best"},
        files=[("photos", ("flow.jpg", b"\xff\xd8fake", "image/jpeg"))])
    p = r.json()
    assert p["settings"]["filament_flow_ratio"] == pytest.approx(0.931)
    assert p["sources"]["filament_flow_ratio"] == "calibrated"
    assert p["confidence"]["overall"] > start_conf
    photo = p["calibrations"][0]["photos"][0]
    assert client.get(f"/photos/{photo}").status_code == 200
    assert client.get("/photos/..%2Ft.db").status_code == 404

    # bad photo type
    r = client.post(f"/api/profiles/{p['id']}/calibrations", data={
        "test": "pressure_advance", "inputs": json.dumps({"pa": 0.03})},
        files=[("photos", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400

    # rebuild keeps calibrated + manual values
    p = client.post(f"/api/profiles/{p['id']}/rebuild").json()
    assert p["settings"]["filament_flow_ratio"] == pytest.approx(0.931)
    assert p["settings"]["fan_max_speed"] == 80

    # restore first version
    first = p["versions"][-1]["id"]
    p = client.post(f"/api/profiles/{p['id']}/restore/{first}").json()
    assert p["settings"]["filament_flow_ratio"] == 0.98

    # export + install into Orca
    r = client.get(f"/api/profiles/{p['id']}/export")
    preset = r.json()
    assert preset["inherits"] == "Generic PLA @System"
    target = str(tmp_path / "orca" / "user" / "default" / "filament")
    r = client.post(f"/api/profiles/{p['id']}/install", json={"target_dir": target})
    assert r.status_code == 200
    assert client.post(f"/api/profiles/{p['id']}/install", json={"target_dir": "/etc"}).status_code == 400

    # scan, snapshot, re-import from Orca
    scan = client.get("/api/orca/scan").json()
    assert len(scan["presets"]) == 1
    assert client.post("/api/orca/snapshot").json()["saved"] == 1
    assert client.post("/api/orca/snapshot").json()["saved"] == 0  # unchanged
    imp = client.post("/api/orca/import-path", json={"path": scan["presets"][0]["path"]}).json()
    assert imp["settings"]["nozzle_temperature"] == 215

    # backup + restore
    z = client.get("/api/backup").content
    client.delete(f"/api/profiles/{p['id']}")
    assert len(client.get("/api/profiles").json()) == 1
    r = client.post("/api/backup/restore", files=[("file", ("b.zip", z, "application/zip"))])
    assert r.status_code == 200
    assert len(client.get("/api/profiles").json()) == 2

    zall = zipfile.ZipFile(io.BytesIO(client.get("/api/export-all").content))
    assert len(zall.namelist()) == 2

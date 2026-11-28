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
    assert imp["import"]["new_profile"] is False  # same preset: updated, not duplicated
    assert imp["id"] == p["id"]

    # backup + restore
    z = client.get("/api/backup").content
    client.delete(f"/api/profiles/{p['id']}")
    assert len(client.get("/api/profiles").json()) == 0
    r = client.post("/api/backup/restore", files=[("file", ("b.zip", z, "application/zip"))])
    assert r.status_code == 200
    assert len(client.get("/api/profiles").json()) == 1

    zall = zipfile.ZipFile(io.BytesIO(client.get("/api/export-all").content))
    assert len(zall.namelist()) == 1


@pytest.mark.parametrize("test,inputs,current,expected", [
    ("flow_yolo", {"modifier": "-0.04"}, {"filament_flow_ratio": 0.98}, 0.94),
    ("flow_yolo", {"modifier": "-0,035"}, {"filament_flow_ratio": 1.0}, 0.965),    # comma decimal
    ("flow_yolo", {"modifier": ".015"}, {"filament_flow_ratio": 1.0}, 1.015),
    ("flow_pass1", {"modifier": " -5 "}, {"filament_flow_ratio": 0.98}, 0.931),
    ("pressure_advance", {"method": "pattern", "pa": "0.032"}, {}, 0.032),
    ("pressure_advance", {"method": "pattern", "pa": "0,045"}, {}, 0.045),
    ("pressure_advance", {"method": "tower", "start": "0", "step": "0.002", "height": "16"}, {}, 0.032),
    ("retraction", {"start": "0", "step": "0.1", "height": "7.5"}, {}, 0.75),
    ("max_flow", {"start": "5", "step": "0.5", "height": "22.5", "margin": "10"}, {}, 14.6),
])
def test_calibration_accepts_decimal_text(test, inputs, current, expected):
    assert calibration.compute(test, inputs, current)["new"] == pytest.approx(expected)


@pytest.mark.parametrize("test,inputs,msg", [
    ("pressure_advance", {"pa": "-0.04"}, "can't be negative"),
    ("flow_pass1", {"modifier": "-0.04"}, "YOLO"),
    ("flow_yolo", {"modifier": "-5"}, "pass 1"),
    ("pressure_advance", {"pa": "abc"}, "isn't a number"),
    ("retraction", {"height": ""}, "Fill in"),
])
def test_calibration_helpful_errors(test, inputs, msg):
    with pytest.raises(ValueError, match=msg):
        calibration.compute(test, inputs, {"filament_flow_ratio": 0.98})


def test_every_test_has_full_guidance():
    for t in calibration.tests_for_ui():
        for key in ("orca_menu", "setup", "what_you_get", "measure", "enter", "example", "inputs"):
            assert t.get(key), (t["id"], key)


def test_remove_calibration_entry_and_undo(client):
    f = client.post("/api/filaments", json={"name": "PLA", "material": "PLA"}).json()
    p = client.post("/api/profiles", json={"filament_id": f["id"]}).json()
    pid = p["id"]

    def cal(test, inputs, files=None):
        return client.post(f"/api/profiles/{pid}/calibrations",
                           data={"test": test, "inputs": json.dumps(inputs)}, files=files).json()

    p = cal("flow_yolo", {"modifier": "-0.04"})                       # 0.98 -> 0.94
    p = cal("pressure_advance", {"pa": "0.03"},
            files=[("photos", ("pa.jpg", b"\xff\xd8x", "image/jpeg"))])  # PA 0.03, enabled
    assert p["settings"]["pressure_advance"] == 0.03 and p["settings"]["enable_pressure_advance"] == 1
    pa_entry = p["calibrations"][0]
    photo = pa_entry["photos"][0]

    # undo the PA result: PA and the enable flag go back, flow is untouched
    r = client.delete(f"/api/calibrations/{pa_entry['id']}?undo=true").json()
    assert sorted(r["reverted"]) == ["enable_pressure_advance", "pressure_advance"]
    p = client.get(f"/api/profiles/{pid}").json()
    assert p["settings"]["pressure_advance"] == 0.04 and p["sources"]["pressure_advance"] == "baseline"
    assert p["settings"]["enable_pressure_advance"] == 0
    assert p["settings"]["filament_flow_ratio"] == 0.94
    assert len(p["calibrations"]) == 1
    assert client.get(f"/photos/{photo}").status_code == 404  # photo file removed too

    # edit flow by hand, then undo the flow calibration: the later edit is kept
    client.put(f"/api/profiles/{pid}", json={"settings": {"filament_flow_ratio": 0.95}})
    flow_entry = p["calibrations"][0]
    r = client.delete(f"/api/calibrations/{flow_entry['id']}?undo=true").json()
    assert r["reverted"] == [] and r["kept"] == ["filament_flow_ratio"]
    p = client.get(f"/api/profiles/{pid}").json()
    assert p["settings"]["filament_flow_ratio"] == 0.95 and p["calibrations"] == []

    # plain remove leaves values alone
    p = cal("retraction", {"height": "6"})
    r = client.delete(f"/api/calibrations/{p['calibrations'][0]['id']}").json()
    assert r["reverted"] == []
    assert client.get(f"/api/profiles/{pid}").json()["settings"]["filament_retraction_length"] == 0.6
    assert client.delete("/api/calibrations/9999").status_code == 404


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def test_import_real_preset_creates_filament_and_profile(client):
    raw = (FIXTURES / "Giantarm PLA Silk Silver @Snapmaker U1.json").read_bytes()
    r = client.post("/api/orca/import-file", files=[("files", ("g.json", raw, "application/json"))]).json()
    res = r["results"][0]
    assert res["new_filament"] and res["new_profile"]

    f = client.get("/api/filaments").json()[0]
    assert (f["brand"], f["name"], f["colour_name"], f["material"]) == ("Giantarm", "PLA Silk", "Silver", "PLA Silk")
    assert f["colour_hex"] == "#c9c9c9" and f["price"] == 71.07 and f["profile_count"] == 1
    assert f["spec"]["nozzle_min"] == 190 and f["spec"]["nozzle_max"] == 215 and f["spec"]["bed_min"] == 45

    p = client.get(f"/api/profiles/{res['profile_id']}").json()
    assert p["name"] == "Giantarm PLA Silk Silver @Snapmaker U1"
    assert p["printer"]["name"].startswith("Snapmaker U1")            # matched from "@Snapmaker U1"
    assert p["settings"]["pressure_advance"] == 0.035 and p["settings"]["filament_flow_ratio"] == 0.94
    assert p["sources"]["pressure_advance"] == "imported"
    assert p["inherits"] == "Generic PLA @System"

    # importing again updates instead of duplicating
    r = client.post("/api/orca/import-file", files=[("files", ("g.json", raw, "application/json"))]).json()
    assert r["results"][0]["new_profile"] is False and r["results"][0]["changed"] == []
    assert len(client.get("/api/profiles").json()) == 1 and len(client.get("/api/filaments").json()) == 1

    # junk is reported per file, not a crash
    r = client.post("/api/orca/import-file", files=[("files", ("x.json", b"{nope", "application/json")),
                                                    ("files", ("m.json", b'{"type":"machine"}', "application/json"))]).json()
    assert [x.get("error") for x in r["results"]] == ["Not a valid .json file", "That file isn't an OrcaSlicer filament preset"]


def test_vlottizff_group_on_export_and_round_trip(client):
    raw = (FIXTURES / "Giantarm PLA Silk Silver @Snapmaker U1.json").read_bytes()
    pid = client.post("/api/orca/import-file", files=[("files", ("g.json", raw))]).json()["results"][0]["profile_id"]

    assert client.get("/api/settings/orca-group").json()["name"] == "VlottizFF"  # default
    preset = client.get(f"/api/profiles/{pid}/preview").json()
    assert preset["name"] == "VlottizFF Giantarm PLA Silk Silver @Snapmaker U1"
    assert preset["filament_settings_id"] == [preset["name"]]
    assert preset["filament_vendor"] == ["VlottizFF"]

    # export -> re-import lands on the same profile, brand kept as Giantarm inside the app
    r = client.post("/api/orca/import-file", files=[("files", ("v.json", json.dumps(preset).encode()))]).json()
    assert r["results"][0]["profile_id"] == pid and r["results"][0]["new_profile"] is False
    p = client.get(f"/api/profiles/{pid}").json()
    assert p["settings"]["filament_vendor"] == "Giantarm"

    # switch it off
    client.put("/api/settings/orca-group", json={"name": "VlottizFF", "as_vendor": False, "prefix_names": False})
    preset = client.get(f"/api/profiles/{pid}/preview").json()
    assert preset["name"] == "Giantarm PLA Silk Silver @Snapmaker U1" and preset["filament_vendor"] == ["Giantarm"]
    assert client.put("/api/settings/orca-group", json={"name": "bad/name"}).status_code == 400


def test_export_has_version_and_exact_printer(client):
    raw = (FIXTURES / "Giantarm PLA Silk Silver @Snapmaker U1.json").read_bytes()
    pid = client.post("/api/orca/import-file", files=[("files", ("g.json", raw))]).json()["results"][0]["profile_id"]
    preset = client.get(f"/api/profiles/{pid}/preview").json()
    assert preset["version"] == orca.PRESET_VERSION
    assert all(int(x) >= 0 for x in preset["version"].split("."))
    # exact Orca printer name, so Orca doesn't guess "Snapmaker U1" from the "@" part of the name
    assert preset["compatible_printers"] == ["Snapmaker U1 (0.4 nozzle)"]


def test_active_orca_folder(tmp_path):
    (tmp_path / "user" / "default" / "filament").mkdir(parents=True)
    assert orca.active_user_dir(tmp_path) == tmp_path / "user" / "default" / "filament"
    # logged in: Orca's config names the account folder, which may not have a filament folder yet
    (tmp_path / "OrcaSlicer.conf").write_text('{"app": {"preset_folder": "a1b2c3d4-uuid", "x": 1}}')
    active = tmp_path / "user" / "a1b2c3d4-uuid" / "filament"
    assert orca.active_user_dir(tmp_path) == active
    assert orca.install_dirs(tmp_path)[0] == active
    assert tmp_path / "user" / "default" / "filament" in orca.install_dirs(tmp_path)
    # junk in the config can't send us elsewhere
    (tmp_path / "OrcaSlicer.conf").write_text('{"app": {"preset_folder": "../../etc"}}')
    assert orca.active_user_dir(tmp_path) == tmp_path / "user" / "default" / "filament"


def test_install_into_logged_in_folder(client, tmp_path):
    orca_root = tmp_path / "orca"
    (orca_root / "OrcaSlicer.conf").write_text('{"app": {"preset_folder": "uuid-123"}}')
    raw = (FIXTURES / "Giantarm PLA Silk Silver @Snapmaker U1.json").read_bytes()
    pid = client.post("/api/orca/import-file", files=[("files", ("g.json", raw))]).json()["results"][0]["profile_id"]
    meta = client.get("/api/meta").json()
    from pathlib import Path as _P
    assert _P(meta["orca_active_dir"]).parts[-2:] == ("uuid-123", "filament")
    assert meta["orca_filament_dirs"][0] == meta["orca_active_dir"]
    r = client.post(f"/api/profiles/{pid}/install", json={"target_dir": meta["orca_active_dir"]})
    assert r.status_code == 200
    written = json.loads((orca_root / "user" / "uuid-123" / "filament" /
                          "VlottizFF Giantarm PLA Silk Silver @Snapmaker U1.json").read_text())
    assert written["version"] and written["compatible_printers"] == ["Snapmaker U1 (0.4 nozzle)"]


def test_vlottizff_bundle_matches_orca_format(client):
    raw = (FIXTURES / "Giantarm PLA Silk Silver @Snapmaker U1.json").read_bytes()
    client.post("/api/orca/import-file", files=[("files", ("g.json", raw))])
    f = client.post("/api/filaments", json={"brand": "Acme", "name": "PETG", "material": "PETG"}).json()
    client.post("/api/profiles", json={"filament_id": f["id"], "printer_id": 1})

    r = client.get("/api/export-bundle")
    assert r.status_code == 200
    assert 'filename="VlottizFF.orca_filament"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(z.read("bundle_structure.json"))
    # keys Orca's BundleMetadata::load_from_json reads
    assert manifest["id"] == "vlottizff" and manifest["name"] == "VlottizFF"
    assert manifest["print_presets"] == [] and manifest["printer_presets"] == []
    assert sorted(manifest["filament_presets"]) == sorted(n for n in z.namelist() if n != "bundle_structure.json")
    assert len(manifest["filament_presets"]) == 2
    for fname in manifest["filament_presets"]:
        p = json.loads(z.read(fname))
        # what Orca's import_json_presets requires: name, a parseable version, filament_settings_id, a real parent
        assert p["name"] and not p["name"].startswith("VlottizFF ")   # the bundle carries the group name
        assert all(part.isdigit() for part in p["version"].split("."))
        assert p["filament_settings_id"] == [p["name"]]
        assert p["inherits"].endswith("@System")
        assert p["compatible_printers"] == ["Snapmaker U1 (0.4 nozzle)"]
        assert p["filament_vendor"] == ["VlottizFF"]
    names = {json.loads(z.read(n))["name"] for n in manifest["filament_presets"]}
    assert "Giantarm PLA Silk Silver @Snapmaker U1" in names


def test_bundle_with_no_profiles(client):
    assert client.get("/api/export-bundle").status_code == 400


@pytest.mark.parametrize("inputs,expect", [
    ({"unit": "speed", "best_speed": "110"}, "110 mm/s"),
    ({"best_speed": "120"}, "120 mm/s"),                                    # older entries without a unit
    ({"unit": "flow", "flow": "9"}, "107 mm/s"),                            # default 0.2 × 0.42
    ({"unit": "flow", "flow": "9,5", "layer_height": "0.16", "line_width": "0.45"}, "132 mm/s"),
])
def test_vfa_speed_or_flow(inputs, expect):
    r = calibration.compute("vfa", inputs, {})
    assert r["new"] is None and r["setting"] is None and expect in r["explanation"]


def test_vfa_rejects_bad_values():
    with pytest.raises(ValueError):
        calibration.compute("vfa", {"unit": "flow", "flow": "9", "layer_height": "0"}, {})
    with pytest.raises(ValueError, match="Fill in"):
        calibration.compute("vfa", {"unit": "flow"}, {})


def _calibrate_everything(client, pid):
    for test, inputs in [("temperature", {"best_temp": "215"}), ("flow_yolo", {"modifier": "-0.02"}),
                         ("pressure_advance", {"pa": "0.035"}), ("retraction", {"height": "7"}),
                         ("max_flow", {"height": "20"})]:
        r = client.post(f"/api/profiles/{pid}/calibrations", data={"test": test, "inputs": json.dumps(inputs)})
        assert r.status_code == 200, r.text
    return r.json()


def test_reaching_100_percent(client):
    f = client.post("/api/filaments", json={"name": "PLA", "material": "PLA"}).json()
    pid = client.post("/api/profiles", json={"filament_id": f["id"], "printer_id": 1}).json()["id"]
    p = _calibrate_everything(client, pid)
    conf = p["confidence"]
    assert conf["overall"] < 100
    # only the settings no test covers are left, each says how to fix it
    assert {t["key"] for t in conf["todo"]} == {"hot_plate_temp", "textured_plate_temp", "fan_max_speed"}
    assert all(t["test"] is None and t["tip"] for t in conf["todo"])

    p = client.post(f"/api/profiles/{pid}/confirm", json={"keys": ["hot_plate_temp", "textured_plate_temp", "fan_max_speed"]}).json()
    assert p["confidence"]["overall"] == 100 and p["confidence"]["todo"] == []
    assert p["sources"]["hot_plate_temp_initial_layer"] == "tested"   # relatives confirmed too
    assert p["sources"]["fan_min_speed"] == "tested"
    assert p["sources"]["nozzle_temperature"] == "calibrated"          # confirm never downgrades
    assert p["versions"][0]["reason"].startswith("Confirmed in a print")


def test_todo_points_at_the_right_test(client):
    f = client.post("/api/filaments", json={"name": "PLA", "material": "PLA"}).json()
    p = client.post("/api/profiles", json={"filament_id": f["id"]}).json()
    todo = {t["key"]: t for t in p["confidence"]["todo"]}
    assert todo["pressure_advance"]["test"] == "pressure_advance"
    assert todo["nozzle_temperature"]["test"] == "temperature"
    assert list(todo)[0] in ("filament_flow_ratio", "nozzle_temperature")  # biggest gains first


def test_calibration_survives_export_import_round_trip(client):
    f = client.post("/api/filaments", json={"brand": "Acme", "name": "PETG", "material": "PETG"}).json()
    pid = client.post("/api/profiles", json={"filament_id": f["id"], "printer_id": 1}).json()["id"]
    _calibrate_everything(client, pid)
    client.post(f"/api/profiles/{pid}/confirm", json={"keys": ["fan_max_speed"]})
    preset = client.get(f"/api/profiles/{pid}/preview").json()
    notes = preset["filament_notes"][0]
    assert notes.startswith("Made with Filament Forge.") and "[ff1:" in notes and "Calibrated:" in notes

    # import into an empty app (e.g. another computer): sources come back
    client.delete(f"/api/profiles/{pid}")
    client.delete(f"/api/filaments/{f['id']}")
    r = client.post("/api/orca/import-file", files=[("files", ("p.json", json.dumps(preset).encode()))]).json()
    p = client.get(f"/api/profiles/{r['results'][0]['profile_id']}").json()
    for k in ("nozzle_temperature", "filament_flow_ratio", "pressure_advance", "filament_retraction_length",
              "filament_max_volumetric_speed"):
        assert p["sources"][k] == "calibrated", k
    assert p["sources"]["fan_max_speed"] == "tested"
    assert p["sources"]["filament_density"] == "baseline"   # where it really came from is kept too
    assert "filament_notes" not in p["settings"]


def test_old_files_without_tag_still_import(client):
    raw = (FIXTURES / "Giantarm PLA Silk Silver @Snapmaker U1.json").read_bytes()
    r = client.post("/api/orca/import-file", files=[("files", ("g.json", raw))]).json()
    p = client.get(f"/api/profiles/{r['results'][0]['profile_id']}").json()
    assert set(p["sources"].values()) == {"imported"}


def test_data_dir_is_outside_the_app_and_old_data_moves(tmp_path, monkeypatch):
    from forge import paths
    monkeypatch.delenv("FORGE_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    d = paths.default_data_dir()
    assert paths.app_dir() not in d.parents
    old, new = tmp_path / "app" / "data", tmp_path / "new"
    (old / "ollama" / "models").mkdir(parents=True)
    (old / "forge.db").write_text("db")
    (old / "ollama" / "models" / "blob").write_text("model")
    assert sorted(paths.migrate_old_data(old, new)) == ["forge.db", "ollama"]
    assert (new / "ollama" / "models" / "blob").read_text() == "model" and not (old / "forge.db").exists()
    # never overwrites an existing database
    (old / "forge.db").write_text("older")
    assert paths.migrate_old_data(old, new) == []
    assert (new / "forge.db").read_text() == "db"
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "custom"))
    assert paths.default_data_dir() == tmp_path / "custom"


def test_retraction_warns_when_almost_zero():
    r = calibration.compute("retraction", {"start": "0", "step": "0.1", "height": "0.1"}, {})
    assert r["new"] == 0.01 and "height box" in r["extra"]["_warning"]
    assert "_warning" not in calibration.compute("retraction", {"height": "7"}, {})["extra"]

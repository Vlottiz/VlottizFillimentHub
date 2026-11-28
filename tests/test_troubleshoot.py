import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from forge import troubleshoot as ts
from forge.db import DB


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCA_CONFIG_DIR", str(tmp_path / "orca"))
    from forge import app as app_mod
    monkeypatch.setattr(app_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_mod, "PHOTO_DIR", tmp_path / "photos")
    app = app_mod.create_app(tmp_path / "t.db", manage_ai=False)
    c = TestClient(app)
    c.app_ref = app
    return c


def _profile(client, material="PLA", **spec):
    f = client.post("/api/filaments", json={"name": "Test", "material": material, "spec": spec}).json()
    return client.post("/api/profiles", json={"filament_id": f["id"], "printer_id": 1}).json()


def test_rules_are_small_clamped_and_limited():
    s = {"nozzle_temperature": 195, "nozzle_temperature_range_low": 190, "nozzle_temperature_range_high": 215,
         "filament_retraction_length": 0.7, "filament_flow_ratio": 0.98, "pressure_advance": 0.04}
    c = ts.plan_changes(["stringing"], s)
    assert [(x["key"], x["new"]) for x in c] == [("nozzle_temperature", 190), ("filament_retraction_length", 0.9)]
    # already at the bottom of the range: temperature can't go lower, so it's skipped
    s2 = dict(s, nozzle_temperature=190)
    assert [x["key"] for x in ts.plan_changes(["stringing"], s2)] == ["filament_retraction_length"]
    # never more than MAX_CHANGES real changes, even for several problems
    many = ts.plan_changes(["stringing", "over_extrusion", "bulging_corners"], s)
    assert len([x for x in many if not x.get("companion")]) == ts.MAX_CHANGES
    # printer hotend limit wins
    hot = ts.plan_changes(["layer_separation"], {"nozzle_temperature": 290, "nozzle_temperature_range_high": 300},
                          {"nozzle_max_temp": 300})
    assert hot[0]["new"] == 295


def test_pressure_advance_fix_enables_it_and_unset_values_start_from_default():
    c = ts.plan_changes(["bulging_corners"], {"enable_pressure_advance": 0})
    keys = {x["key"]: x["new"] for x in c}
    assert keys == {"pressure_advance": 0.05, "enable_pressure_advance": 1}
    r = ts.plan_changes(["stringing"], {"nozzle_temperature": 210})
    assert {x["key"]: x["new"] for x in r}["filament_retraction_length"] == 1.0  # 0.8 default + 0.2


def test_tip_only_problems_change_nothing():
    assert ts.plan_changes(["ringing", "wet_filament"], {"nozzle_temperature": 210}) == []


def test_ai_output_is_filtered_to_the_list():
    def chat(messages, schema):
        assert schema["properties"]["issues"]["items"]["properties"]["id"]["enum"] == list(ts.ISSUES)
        assert "images" in messages[1] and "PLA" in messages[1]["content"] and "strings" in messages[1]["content"]
        return json.dumps({"issues": [
            {"id": "stringing", "confidence": 0.9, "evidence": "webs between towers"},
            {"id": "invented_problem", "confidence": 0.99, "evidence": "x"},
            {"id": "stringing", "confidence": 0.5, "evidence": "dupe"},
            {"id": "blobs_zits", "confidence": 7, "evidence": "bumps"}], "summary": "Stringing."})
    d = ts.diagnose_with_ai(chat, "lots of strings", [b"\xff\xd8img"], "PLA", {"nozzle_temperature": 210})
    assert [i["id"] for i in d["issues"]] == ["blobs_zits", "stringing"]  # sorted, filtered, clamped
    assert d["issues"][0]["confidence"] == 1.0


def test_full_flow_ai_trial_then_better(client, monkeypatch):
    p = _profile(client, nozzle_min=190, nozzle_max=220)
    pid, temp0 = p["id"], p["settings"]["nozzle_temperature"]
    ai = client.app_ref.state.ai
    monkeypatch.setattr(ai, "ready", lambda: True)
    monkeypatch.setattr(ai, "chat", lambda m, s: json.dumps(
        {"issues": [{"id": "stringing", "confidence": 0.85, "evidence": "fine strings"}], "summary": "Stringing."}))
    sess = client.post(f"/api/profiles/{pid}/diagnose", data={"description": "strings everywhere"},
                       files=[("photos", ("p.jpg", b"\xff\xd8x", "image/jpeg"))]).json()
    assert sess["diagnosis"]["by"] == "ai" and sess["diagnosis"]["issues"][0]["title"] == "Stringing / oozing"
    assert sess["diagnosis"]["issues"][0]["tips"] and len(sess["photos"]) == 1
    assert {c["key"] for c in sess["changes"]} == {"nozzle_temperature", "filament_retraction_length"}

    trial = client.post(f"/api/troubleshoots/{sess['id']}/trial").json()
    assert trial["trial_of"] == pid and "(trial: stringing)" in trial["name"]
    assert trial["settings"]["nozzle_temperature"] == temp0 - 5
    assert trial["trial"]["original"]["id"] == pid
    assert trial["trial"]["session"]["diagnosis"]["issues"][0]["title"] == "Stringing / oozing"
    assert client.get(f"/api/profiles/{pid}").json()["settings"]["nozzle_temperature"] == temp0  # original untouched
    # asking again returns the same trial, not a second one
    assert client.post(f"/api/troubleshoots/{sess['id']}/trial").json()["id"] == trial["id"]

    orig = client.post(f"/api/profiles/{trial['id']}/trial/result", json={"result": "better"}).json()
    assert orig["id"] == pid and orig["settings"]["nozzle_temperature"] == temp0 - 5
    assert orig["sources"]["nozzle_temperature"] == "tested"
    assert orig["sources"]["filament_retraction_length"] == "tested"
    assert orig["versions"][0]["reason"].startswith("Fix confirmed in a print")
    assert orig["troubleshoots"][0]["status"] == "fixed"
    assert orig["troubleshoots"][0]["diagnosis"]["issues"][0]["title"] == "Stringing / oozing"
    assert client.get(f"/api/profiles/{trial['id']}").status_code == 404
    assert len(client.get("/api/profiles").json()) == 1


def test_manual_pick_not_better_and_keep(client):
    pid = _profile(client)["id"]
    sess = client.post(f"/api/profiles/{pid}/diagnose", data={"issues": "warping", "use_ai": "false"}).json()
    assert sess["diagnosis"]["by"] == "you" and sess["changes"][0]["key"] == "textured_plate_temp"
    t = client.post(f"/api/troubleshoots/{sess['id']}/trial").json()
    p = client.post(f"/api/profiles/{t['id']}/trial/result", json={"result": "not_better"}).json()
    assert p["id"] == pid and p["troubleshoots"][0]["status"] == "not_better"
    assert p["sources"]["textured_plate_temp"] == "baseline"  # unchanged

    sess = client.post(f"/api/profiles/{pid}/diagnose", data={"issues": "over_extrusion", "use_ai": "false"}).json()
    t = client.post(f"/api/troubleshoots/{sess['id']}/trial").json()
    kept = client.post(f"/api/profiles/{t['id']}/trial/result", json={"result": "keep"}).json()
    assert kept["trial_of"] is None and "trial" not in kept["name"]
    assert len(client.get("/api/profiles").json()) == 2


def test_diagnose_errors(client):
    pid = _profile(client)["id"]
    assert client.post(f"/api/profiles/{pid}/diagnose", data={"description": "help"}).status_code == 409  # AI off
    assert client.post(f"/api/profiles/{pid}/diagnose", data={"use_ai": "false"}).status_code == 400
    assert client.post(f"/api/profiles/{pid}/diagnose", data={"issues": "ringing", "use_ai": "false"}).json()["changes"] == []
    sid = client.post(f"/api/profiles/{pid}/diagnose", data={"issues": "ringing", "use_ai": "false"}).json()["id"]
    assert client.post(f"/api/troubleshoots/{sid}/trial").status_code == 400
    assert client.post(f"/api/profiles/{pid}/trial/result", json={"result": "better"}).status_code == 400
    r = client.post(f"/api/profiles/{pid}/diagnose", data={"issues": "warping", "use_ai": "false"},
                    files=[("photos", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY, filament_id INTEGER, printer_id INTEGER, name TEXT NOT NULL,"
                " material TEXT, inherits TEXT, settings TEXT NOT NULL DEFAULT '{}', sources TEXT NOT NULL DEFAULT '{}',"
                " notes TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL)")
    con.commit(); con.close()
    db = DB(path)
    assert "trial_of" in {r[1] for r in db.conn.execute("PRAGMA table_info(profiles)")}
    assert db.conn.execute("SELECT name FROM sqlite_master WHERE name='troubleshoots'").fetchone()


def test_near_zero_retraction_jumps_to_a_real_value():
    c = {x["key"]: x for x in ts.plan_changes(["stringing"], {"nozzle_temperature": 195,
         "nozzle_temperature_range_low": 190, "filament_retraction_length": 0.01})}
    assert c["filament_retraction_length"]["new"] == 0.6
    assert "almost zero" in c["filament_retraction_length"]["why"]
    c = {x["key"]: x for x in ts.plan_changes(["stringing"], {"filament_retraction_length": 0.7})}
    assert c["filament_retraction_length"]["new"] == 0.9


def test_user_words_beat_wrong_ai_guesses():
    # roughly what the AI said about the silk Benchy
    ai = [{"id": "sagging_overhangs", "confidence": 0.5, "evidence": "droop"},
          {"id": "warping", "confidence": 0.4, "evidence": "corner"},
          {"id": "stringing", "confidence": 0.7, "evidence": "hairs in cabin"}]
    out = ts.combine(ai, "there are wavy lines on the hull and some strings inside")
    ids = [i["id"] for i in out]
    assert ids[0] == "stringing" and out[0]["confidence"] == 1.0
    assert "vfa" in ids and "warping" not in ids   # added from words; low guess dropped
    assert len(out) <= 3
    assert ts.combine([{"id": "warping", "confidence": 0.3, "evidence": ""}], "") == []


def test_vfa_suggests_speed_from_your_test():
    s = ts.process_suggestions(["vfa"], {"vfa_speed": 107.1})
    assert s[0]["setting"] == "Outer wall speed" and s[0]["value"].startswith("107 mm/s")
    assert "run the VFA calibration" in ts.process_suggestions(["vfa"])[0]["value"]
    assert ts.plan_changes(["vfa"], {"nozzle_temperature": 200}) == []


def test_diagnose_shrinks_photos_and_combines(client, monkeypatch, tmp_path):
    import io
    from PIL import Image
    pid = _profile(client)["id"]
    client.post(f"/api/profiles/{pid}/calibrations", data={"test": "vfa", "inputs": json.dumps({"unit": "flow", "flow": "9"})})
    ai = client.app_ref.state.ai
    seen = {}

    def chat(messages, schema):
        import base64
        imgs = messages[1]["images"]
        seen["sizes"] = [Image.open(io.BytesIO(base64.b64decode(b))).size for b in imgs]
        seen["prompt"] = messages[1]["content"]
        return json.dumps({"issues": [{"id": "sagging_overhangs", "confidence": 0.5, "evidence": "?"},
                                      {"id": "warping", "confidence": 0.35, "evidence": "?"}], "summary": ""})
    monkeypatch.setattr(ai, "ready", lambda: True)
    monkeypatch.setattr(ai, "chat", chat)
    big = io.BytesIO(); Image.new("RGB", (1126, 2000), (200, 200, 200)).save(big, "JPEG")
    sess = client.post(f"/api/profiles/{pid}/diagnose", data={"description": "wavy lines on the hull"},
                       files=[("photos", ("a.jpg", big.getvalue(), "image/jpeg")),
                              ("photos", ("b.jpg", big.getvalue(), "image/jpeg"))]).json()
    assert all(max(s) <= 1024 for s in seen["sizes"]) and len(seen["sizes"]) == 2
    assert seen["prompt"].startswith('What the user says about the print: "wavy lines on the hull"')
    ids = [i["id"] for i in sess["diagnosis"]["issues"]]
    assert ids[0] == "vfa" and "warping" not in ids
    assert sess["diagnosis"]["process"][0]["value"].startswith("107 mm/s")

    # untick the wrong guess, add stringing: suggestions are worked out again
    r = client.post(f"/api/troubleshoots/{sess['id']}/replan", json={"issues": ["vfa", "stringing"]}).json()
    assert [i["id"] for i in r["diagnosis"]["issues"]] == ["vfa", "stringing"]
    assert {c["key"] for c in r["changes"]} == {"nozzle_temperature", "filament_retraction_length"}
    assert r["diagnosis"]["issues"][1]["title"] == "Stringing / oozing"
    client.post(f"/api/troubleshoots/{sess['id']}/trial")
    assert client.post(f"/api/troubleshoots/{sess['id']}/replan", json={"issues": ["vfa"]}).status_code == 400


def test_context_setting(client):
    assert client.put("/api/ai/settings", json={"context": 32768}).json()["settings"]["context"] == 32768
    assert client.put("/api/ai/settings", json={"context": 999}).status_code == 400


def test_shrink_keeps_small_and_bad_images():
    import io
    from PIL import Image
    from forge.ai import images
    small = io.BytesIO(); Image.new("RGB", (300, 200)).save(small, "PNG")
    out = Image.open(io.BytesIO(images.shrink(small.getvalue())))
    assert out.size == (300, 200)
    assert images.shrink(b"not an image") == b"not an image"
    assert images.token_estimate(small.getvalue()) < 100

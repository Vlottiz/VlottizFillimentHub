import io
import json
import shutil
import subprocess
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from forge import orca, swatch

DOCS = Path(__file__).resolve().parent.parent / "docs"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCA_CONFIG_DIR", str(tmp_path / "orca"))
    from forge import app as app_mod
    monkeypatch.setattr(app_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_mod, "PHOTO_DIR", tmp_path / "photos")
    return TestClient(app_mod.create_app(tmp_path / "t.db", manage_ai=False))


def _calibrated_profile(client):
    raw = (FIXTURES / "Giantarm PLA Silk Silver @Snapmaker U1.json").read_bytes()
    pid = client.post("/api/orca/import-file", files=[("files", ("g.json", raw))]).json()["results"][0]["profile_id"]
    for test, inputs in [("temperature", {"best_temp": "200"}), ("pressure_advance", {"pa": "0.038"})]:
        client.post(f"/api/profiles/{pid}/calibrations", data={"test": test, "inputs": json.dumps(inputs)})
    client.post(f"/api/profiles/{pid}/confirm", json={"keys": ["fan_max_speed"]})
    return pid


def test_link_round_trip_python(client):
    pid = _calibrated_profile(client)
    q = client.get(f"/api/profiles/{pid}/qr?code=full").json()
    assert q["url"].startswith("https://vlottiz.github.io/VlottizFillimentHub/#1*")
    d = swatch.decode(q["url"].split("#", 1)[1])
    assert d["name"] == "Giantarm PLA Silk Silver @Snapmaker U1" and d["group"] == "VlottizFF"
    assert d["printer"] == "Snapmaker U1 (0.4 nozzle)" and d["inherits"] == "Generic PLA @System"
    assert d["settings"]["pressure_advance"] == 0.038 and d["settings"]["default_filament_colour"] == "#c9c9c9"
    assert d["sources"]["pressure_advance"] == "calibrated" and d["sources"]["fan_max_speed"] == "tested"
    # small enough to print: well under QR version 15 at error level M
    assert q["modules"] <= 77 and q["chars"] < 400
    assert q["module_mm"] >= 0.7


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_scan_page_rebuilds_the_same_preset_as_the_app(client):
    """docs/ff.js (what the phone runs) must produce exactly the app's export."""
    pid = _calibrated_profile(client)
    url = client.get(f"/api/profiles/{pid}/qr?code=full").json()["url"]
    app_json = client.get(f"/api/profiles/{pid}/preview").json()
    app_bundle = zipfile.ZipFile(io.BytesIO(client.get("/api/export-bundle").content))
    app_bundle_preset = json.loads(app_bundle.read([n for n in app_bundle.namelist() if n != "bundle_structure.json"][0]))
    script = f"""
      const FF = require({json.dumps(str(DOCS / 'ff.js'))});
      const d = FF.decode({json.dumps(url.split('#', 1)[1])});
      const b = FF.bundleFiles(d);
      const dec = new TextDecoder();
      console.log(JSON.stringify({{ json: FF.buildPreset(d, false), bundle: FF.buildPreset(d, true),
        manifest: JSON.parse(dec.decode(b.files[0].data)), names: b.files.map(f => f.name) }}));
    """
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out["json"] == app_json
    assert out["bundle"] == app_bundle_preset
    assert out["manifest"]["id"] == "vlottizff" and out["manifest"]["filament_presets"] == [out["names"][1]]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_scan_page_zip_is_valid(tmp_path):
    script = f"""
      const FF = require({json.dumps(str(DOCS / 'ff.js'))});
      const enc = new TextEncoder();
      FF.zip([{{name: "a.json", data: enc.encode('{{"x":1}}')}}, {{name: "b c.json", data: enc.encode("hello")}}])
        .arrayBuffer().then(b => process.stdout.write(Buffer.from(b).toString("base64")));
    """
    raw = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    import base64
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(raw)))
    assert z.testzip() is None
    assert z.read("a.json") == b'{"x":1}' and z.read("b c.json") == b"hello"


def _mesh_ok(mesh):
    """Closed surface: every edge is shared by an even number of faces, and volume is positive."""
    edges = Counter()
    for a, b, c in mesh.tris:
        for e in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted(e))] += 1
    vol = 0.0
    for a, b, c in mesh.tris:
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = mesh.verts[a], mesh.verts[b], mesh.verts[c]
        vol += (x1 * (y2 * z3 - y3 * z2) - x2 * (y1 * z3 - y3 * z1) + x3 * (y1 * z2 - y2 * z1)) / 6
    return all(n % 2 == 0 for n in edges.values()), vol


@pytest.mark.parametrize("style", ["two_colour", "hollow"])
def test_swatch_model_is_solid_and_sized(style):
    m = swatch.qr_matrix("https://example.com/#" + "x" * 150)
    S = swatch.plate_size(len(m), 60)  # grown so squares are at least 1.5 mm
    assert S > 60 and S / (len(m) + 2 * swatch.QUIET) >= swatch.MIN_MODULE_MM
    parts, cell = swatch.build_parts(m, 60, 5, style)
    assert cell >= swatch.MIN_MODULE_MM
    total_vol = 0
    for name, mesh in parts:
        closed, vol = _mesh_ok(mesh)
        assert closed, name
        assert vol > 0, name  # faces point outwards
        xs = [v[0] for v in mesh.verts]; zs = [v[2] for v in mesh.verts]
        assert max(xs) <= S + 1e-4 and min(zs) >= 0 and max(zs) <= 5.0001
        total_vol += vol
    dark = sum(map(sum, m)) * cell * cell
    if style == "two_colour":
        assert [n for n, _ in parts] == ["Swatch", "QR code (second colour)"]
        # body + inlay fill the full plate exactly
        assert total_vol == pytest.approx(S * S * 5, rel=1e-6)
        assert _mesh_ok(parts[1][1])[1] == pytest.approx(dark * swatch.INLAY_DEPTH, rel=1e-6)
    else:
        assert total_vol == pytest.approx(S * S * 5 - dark * (5 - swatch.HOLLOW_FLOOR), rel=1e-6)
    # a plate bigger than the minimum is kept as asked
    assert swatch.plate_size(len(m), 150) == 150


def test_swatch_3mf_download(client):
    pid = _calibrated_profile(client)
    r = client.get(f"/api/profiles/{pid}/swatch.3mf?style=two_colour&size=60&thickness=5")
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('QR swatch.3mf"')
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    root = ET.fromstring(z.read("3D/3dmodel.model"))
    assert root.get("unit") == "millimeter"
    objs = root.findall("m:resources/m:object", ns)
    assert [o.get("name") for o in objs][:3] == ["Swatch", "Name plate", "QR code and name (second colour)"]
    assert len(objs[3].findall("m:components/m:component", ns)) == 3
    assert root.find("m:build/m:item", ns).get("objectid") == objs[3].get("id")
    # no label when asked for none
    z2 = zipfile.ZipFile(io.BytesIO(client.get(f"/api/profiles/{pid}/swatch.3mf?label=").content))
    names = [o.get("name") for o in ET.fromstring(z2.read("3D/3dmodel.model")).findall("m:resources/m:object", ns)]
    assert names[:2] == ["Swatch", "QR code (second colour)"]
    assert client.get(f"/api/profiles/{pid}/swatch.3mf?style=nope").status_code == 400
    assert client.get(f"/api/profiles/{pid}/swatch.3mf?size=5").status_code == 400


def test_qr_base_url_setting(client):
    pid = _calibrated_profile(client)
    assert client.put("/api/settings/qr", json={"base_url": "not a url"}).status_code == 400
    client.put("/api/settings/qr", json={"base_url": "https://example.org/ff/index.html"})
    assert client.get(f"/api/profiles/{pid}/qr?code=full").json()["url"].startswith("https://example.org/ff/index.html#1*")


def test_fields_lists_match():
    js = (DOCS / "ff.js").read_text()
    for k in orca.QR_FIELDS_V1:
        assert f'"{k}"' in js
    assert js.count('"filament_') + js.count('"nozzle_') > 0
    assert orca.PRESET_VERSION in js


@pytest.mark.parametrize("style", ["two_colour", "hollow"])
def test_swatch_with_name_label(style):
    m = swatch.qr_matrix("https://example.com/#" + "x" * 150)
    S = swatch.plate_size(len(m), 60)
    parts, cell = swatch.build_parts(m, 60, 5, style, "Giantarm PLA Silk Silver")
    label_h = len(swatch.label_mask("Giantarm PLA Silk Silver", S)) * swatch.LABEL_PX
    assert 5 < label_h < 20
    total = 0
    for name, mesh in parts:
        closed, vol = _mesh_ok(mesh)
        assert closed and vol > 0, name
        ys = [v[1] for v in mesh.verts]
        assert min(ys) >= -1e-6 and max(ys) <= S + label_h + 1e-6
        total += vol
    if style == "two_colour":
        assert total == pytest.approx(S * (S + label_h) * 5, rel=1e-6)   # parts fill the plate exactly
    else:
        assert total < S * (S + label_h) * 5


def test_label_fits_and_long_names_are_shortened():
    assert swatch.label_text("Giantarm PLA Silk Silver @Snapmaker U1") == "Giantarm PLA Silk Silver"
    short = swatch.label_mask("PLA", 60)
    long = swatch.label_mask("An Extremely Long Filament Brand Name With Lots Of Words In It Really", 60)
    for mask in (short, long):
        assert len(mask[0]) == round(60 / swatch.LABEL_PX)
        assert any(any(r) for r in mask)
        # 2 mm clear margin on both sides
        edge = round(2 / swatch.LABEL_PX)
        assert not any(r[:edge - 1] and any(r[:edge - 1]) for r in mask)
        assert not any(any(r[-(edge - 1):]) for r in mask)
    assert len(long) <= len(swatch.label_mask("Two lines of text here please", 60)) + 2
    assert swatch.label_mask("   ", 60) == []


def test_import_from_swatch_link(client):
    pid = _calibrated_profile(client)
    url = client.get(f"/api/profiles/{pid}/qr?code=full").json()["url"]
    before = client.get(f"/api/profiles/{pid}").json()
    client.delete(f"/api/profiles/{pid}")
    r = client.post("/api/import-swatch-link", json={"link": "  " + url + "\n"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == before["name"] and p["import"]["new_profile"]
    assert p["printer"]["name"].startswith("Snapmaker U1")
    for k in ("pressure_advance", "nozzle_temperature", "filament_flow_ratio"):
        assert p["settings"][k] == before["settings"][k]
    assert p["sources"]["pressure_advance"] == "calibrated" and p["sources"]["fan_max_speed"] == "tested"
    assert client.post("/api/import-swatch-link", json={"link": "https://example.com/#nope"}).status_code == 400


def _photo_of_swatch(parts, plate_colour=(198, 198, 200), qr_colour=(70, 70, 75), scale=8, blur=1.2, angle=7):
    """Rough 'phone photo' of the printed swatch: top faces only, then blurred, rotated, JPEG'd."""
    from PIL import Image, ImageDraw, ImageFilter
    H = max(v[1] for _, m in parts for v in m.verts)
    W = max(v[0] for _, m in parts for v in m.verts)
    img = Image.new("RGB", (int(W * scale) + 80, int(H * scale) + 80), (40, 30, 30))   # dark desk around it
    d = ImageDraw.Draw(img)
    top = max(v[2] for _, m in parts for v in m.verts)
    for name, mesh in parts:
        col = qr_colour if "second colour" in name else plate_colour
        for a, b, c in mesh.tris:
            p = [mesh.verts[i] for i in (a, b, c)]
            if all(abs(q[2] - top) < 1e-6 for q in p):
                d.polygon([(40 + q[0] * scale, 40 + (H - q[1]) * scale) for q in p], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(blur)).rotate(angle, expand=True, fillcolor=(40, 30, 30))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=70)
    return out.getvalue()


@pytest.mark.parametrize("angle,blur", [(0, 0.5), (7, 1.2), (-12, 1.5)])
def test_read_qr_from_a_photo_of_the_printed_swatch(client, angle, blur):
    pid = _calibrated_profile(client)
    url = client.get(f"/api/profiles/{pid}/qr?code=full").json()["url"]
    parts, _ = swatch.build_parts(swatch.qr_matrix(url), 60, 5, "two_colour", "Giantarm PLA Silk Silver")
    photo = _photo_of_swatch(parts, angle=angle, blur=blur)
    assert swatch.read_qr(photo) == url


def test_scan_swatch_endpoint_imports_it(client):
    pid = _calibrated_profile(client)
    url = client.get(f"/api/profiles/{pid}/qr?code=full").json()["url"]
    before = client.get(f"/api/profiles/{pid}").json()
    client.delete(f"/api/profiles/{pid}")
    parts, _ = swatch.build_parts(swatch.qr_matrix(url), 60, 5, "two_colour", "Giantarm")
    r = client.post("/api/scan-swatch", files={"file": ("swatch.jpg", _photo_of_swatch(parts), "image/jpeg")})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == before["name"] and p["link"] == url
    assert p["settings"]["pressure_advance"] == before["settings"]["pressure_advance"]
    assert p["sources"]["pressure_advance"] == "calibrated"
    # just read it without importing (webcam preview)
    r = client.post("/api/scan-swatch", data={"import_now": "false"},
                    files={"file": ("s.jpg", _photo_of_swatch(parts), "image/jpeg")})
    assert r.json() == {"link": url}
    # a photo with no code in it
    from PIL import Image
    blank = io.BytesIO(); Image.new("RGB", (400, 300), (120, 120, 120)).save(blank, "JPEG")
    assert client.post("/api/scan-swatch", files={"file": ("x.jpg", blank.getvalue(), "image/jpeg")}).status_code == 422
    assert client.post("/api/scan-swatch", files={"file": ("x.jpg", b"junk", "image/jpeg")}).status_code == 422


def test_root_redirect_page_keeps_the_swatch_data():
    root = DOCS.parent / "index.html"
    html = root.read_text()
    assert "docs/" in html and "location.hash" in html


def test_swatch_check_flow(client, tmp_path):
    pid = _calibrated_profile(client)
    url = client.get(f"/api/profiles/{pid}/qr?code=full").json()["url"]

    # same profile in the app, not in Orca yet
    c = client.post("/api/swatch/check", json={"link": url}).json()
    assert c["app"]["status"] == "same" and c["app"]["profile_id"] == pid
    assert c["orca"]["installed"] is False
    assert c["swatch"]["name"] == "Giantarm PLA Silk Silver @Snapmaker U1"
    assert "pressure_advance" in c["swatch"]["calibrated"]
    u1 = c["printers"][0]
    assert u1["matches"] and u1["toolheads"] == 4 and u1["loaded"] == {"1": None, "2": None, "3": None, "4": None}

    # the app's copy changed since the swatch was printed: differences are listed
    client.put(f"/api/profiles/{pid}", json={"settings": {"nozzle_temperature": 205}})
    c = client.post("/api/swatch/check", json={"link": url}).json()
    assert c["app"]["status"] == "different"
    assert c["app"]["diffs"] == [{"key": "nozzle_temperature", "label": "Nozzle", "app": 205, "swatch": 200}]
    # take the swatch's values
    client.post("/api/swatch/apply", json={"link": url, "action": "update"})
    assert client.post("/api/swatch/check", json={"link": url}).json()["app"]["status"] == "same"
    assert client.post("/api/swatch/apply", json={"link": url, "action": "add"}).status_code == 400

    # in Orca after installing
    orca_dir = tmp_path / "orca" / "user" / "default" / "filament"
    orca_dir.mkdir(parents=True, exist_ok=True)
    client.post(f"/api/profiles/{pid}/install", json={"target_dir": str(orca_dir)})
    c = client.post("/api/swatch/check", json={"link": url}).json()
    assert c["orca"]["installed"] and c["orca"]["up_to_date"]

    # load it into toolhead 2
    pr = client.put(f"/api/printers/{u1['id']}/toolheads/2", json={"profile_id": pid}).json()
    assert pr["data"]["loaded"]["2"]["profile_id"] == pid
    assert client.get(f"/api/profiles/{pid}").json()["loaded_in"] == [
        {"printer_id": u1["id"], "printer": u1["name"], "toolhead": 2}]
    c = client.post("/api/swatch/check", json={"link": url}).json()
    assert c["printers"][0]["loaded"]["2"]["name"] == "Giantarm PLA Silk Silver @Snapmaker U1"
    assert client.put(f"/api/printers/{u1['id']}/toolheads/9", json={"profile_id": pid}).status_code == 400
    # unload; and deleting a profile clears it from toolheads
    client.put(f"/api/printers/{u1['id']}/toolheads/2", json={"profile_id": None})
    assert client.post("/api/swatch/check", json={"link": url}).json()["printers"][0]["loaded"]["2"] is None
    client.put(f"/api/printers/{u1['id']}/toolheads/3", json={"profile_id": pid})
    client.delete(f"/api/profiles/{pid}")
    assert client.get("/api/printers").json()[0]["data"]["loaded"] == {}


def test_swatch_check_new_filament(client):
    pid = _calibrated_profile(client)
    url = client.get(f"/api/profiles/{pid}/qr?code=full").json()["url"]
    client.delete(f"/api/profiles/{pid}")
    c = client.post("/api/swatch/check", json={"link": url}).json()
    assert c["app"]["status"] == "missing" and c["orca"]["installed"] is False
    p = client.post("/api/swatch/apply", json={"link": url, "action": "add"}).json()
    assert p["sources"]["pressure_advance"] == "calibrated"
    assert client.post("/api/swatch/check", json={"link": url}).json()["app"]["status"] == "same"
    assert client.post("/api/swatch/check", json={"link": "https://x/#nonsense"}).status_code == 400


def test_quick_scan_code_is_small_and_finds_the_profile(client, tmp_path):
    pid = _calibrated_profile(client)
    short = client.get(f"/api/profiles/{pid}/qr?code=short").json()
    full = client.get(f"/api/profiles/{pid}/qr").json()  # full settings is the default
    assert full["url"].split("#", 1)[1].startswith("1*")
    assert "#s*Giantarm+PLA+Silk+Silver+@Snapmaker+U1*PLA+Silk*c9c9c9" in short["url"]
    # squares are never under 1.5 mm: the plate grows to fit the code
    assert short["module_mm"] >= 1.5 and full["module_mm"] >= 1.5
    assert short["modules"] <= 41 and short["size_mm"] <= 71
    assert full["modules"] > 55 and full["size_mm"] >= 100 and full["min_size_mm"] == full["size_mm"]
    assert client.get(f"/api/profiles/{pid}/qr?size=150").json()["size_mm"] == 150
    d = swatch.decode(short["url"].split("#", 1)[1])
    assert d["short"] and d["name"] == "Giantarm PLA Silk Silver @Snapmaker U1"
    assert d["settings"]["filament_type"] == "PLA Silk" and d["settings"]["default_filament_colour"].startswith("#")

    # the swatch check finds it in the app (nothing to compare)
    c = client.post("/api/swatch/check", json={"link": short["url"]}).json()
    assert c["swatch"]["short"] and c["app"]["status"] == "same" and c["app"]["profile_id"] == pid
    # importing it just opens the existing profile
    assert client.post("/api/import-swatch-link", json={"link": short["url"]}).json()["id"] == pid
    # the 3mf uses it too
    r = client.get(f"/api/profiles/{pid}/swatch.3mf?size=60&code=short")
    assert r.status_code == 200
    assert client.get(f"/api/profiles/{pid}/swatch.3mf?code=nope").status_code == 400

    # someone else's quick-scan swatch: it can't be added, and says why
    client.delete(f"/api/profiles/{pid}")
    c = client.post("/api/swatch/check", json={"link": short["url"]}).json()
    assert c["app"] == {"status": "missing", "can_add": False}
    r = client.post("/api/swatch/apply", json={"link": short["url"], "action": "add"})
    assert r.status_code == 400 and "only carries the filament's name" in r.json()["error"]
    assert client.post("/api/import-swatch-link", json={"link": short["url"]}).status_code == 400


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_scan_page_reads_quick_scan_links():
    script = f"""
      const FF = require({json.dumps(str(DOCS / 'ff.js'))});
      console.log(JSON.stringify(FF.decode("#s*Giantarm+PLA+Silk+Silver+@Snapmaker+U1*PLA*C0C0C0")));
    """
    d = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert d["short"] and d["name"] == "Giantarm PLA Silk Silver @Snapmaker U1"
    assert d["settings"] == {"filament_type": "PLA", "default_filament_colour": "#C0C0C0"}

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
    q = client.get(f"/api/profiles/{pid}/qr").json()
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
    url = client.get(f"/api/profiles/{pid}/qr").json()["url"]
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
    parts, cell = swatch.build_parts(m, 60, 5, style)
    total_vol = 0
    for name, mesh in parts:
        closed, vol = _mesh_ok(mesh)
        assert closed, name
        assert vol > 0, name  # faces point outwards
        xs = [v[0] for v in mesh.verts]; zs = [v[2] for v in mesh.verts]
        assert max(xs) <= 60.0001 and min(zs) >= 0 and max(zs) <= 5.0001
        total_vol += vol
    dark = sum(map(sum, m)) * cell * cell
    if style == "two_colour":
        assert [n for n, _ in parts] == ["Swatch", "QR code (second colour)"]
        # body + inlay fill the full plate exactly
        assert total_vol == pytest.approx(60 * 60 * 5, rel=1e-6)
        assert _mesh_ok(parts[1][1])[1] == pytest.approx(dark * swatch.INLAY_DEPTH, rel=1e-6)
    else:
        assert total_vol == pytest.approx(60 * 60 * 5 - dark * (5 - swatch.HOLLOW_FLOOR), rel=1e-6)


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
    assert [o.get("name") for o in objs][:2] == ["Swatch", "QR code (second colour)"]
    assert len(objs[2].findall("m:components/m:component", ns)) == 2
    assert root.find("m:build/m:item", ns).get("objectid") == objs[2].get("id")
    assert client.get(f"/api/profiles/{pid}/swatch.3mf?style=nope").status_code == 400
    assert client.get(f"/api/profiles/{pid}/swatch.3mf?size=5").status_code == 400


def test_qr_base_url_setting(client):
    pid = _calibrated_profile(client)
    assert client.put("/api/settings/qr", json={"base_url": "not a url"}).status_code == 400
    client.put("/api/settings/qr", json={"base_url": "https://example.org/ff/index.html"})
    assert client.get(f"/api/profiles/{pid}/qr").json()["url"].startswith("https://example.org/ff/index.html#1*")


def test_fields_lists_match():
    js = (DOCS / "ff.js").read_text()
    for k in orca.QR_FIELDS_V1:
        assert f'"{k}"' in js
    assert js.count('"filament_') + js.count('"nozzle_') > 0
    assert orca.PRESET_VERSION in js

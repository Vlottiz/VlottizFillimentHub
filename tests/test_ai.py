import json

import pytest
from fastapi.testclient import TestClient

from forge.ai import extract, runtime

PAGE = """<html><head><title>Acme PLA Pro 1.75mm - Galaxy Black</title>
<meta name="description" content="Tough PLA+ for fast printing">
<script type="application/ld+json">{"@type":"Product","name":"Acme PLA Pro","brand":{"name":"Acme"},
 "offers":{"price":"21.99","priceCurrency":"USD"}}</script>
<script>var junk = "nozzle 999";</script></head>
<body><nav>Home | Shop | Nozzle cleaning kits</nav>
<h1>Acme PLA Pro</h1>
<table><tr><th>Printing Temperature</th><td>205–230℃</td></tr>
<tr><th>Bed Temperature</th><td>50-65 °C</td></tr>
<tr><th>Density</th><td>1,23 g/cm³</td></tr>
<tr><th>Net weight</th><td>1 kg</td></tr></table>
<p>Dry at 55°C for 6 hours before use.</p><footer>Copyright</footer></body></html>"""


def test_html_text_keeps_specs_and_drops_junk():
    t = extract.html_text(PAGE)
    assert "Printing Temperature | 205–230℃" in t
    assert "price: 21.99 USD" in t and "brand: Acme" in t
    assert "999" not in t and "Copyright" not in t and "cleaning kits" not in t


def fake_answer(**over):
    base = {k: {"value": None, "quote": None} for k in extract.FIELDS}
    base.update({
        "brand": {"value": "Acme", "quote": "Acme PLA Pro"},
        "product_name": {"value": "PLA Pro", "quote": "Acme PLA Pro"},
        "material": {"value": "PLA+", "quote": "Tough PLA+"},
        "nozzle_min": {"value": 205, "quote": "205–230℃"},
        "nozzle_max": {"value": 230, "quote": "205-230°C"},     # dash/℃ normalised
        "bed_min": {"value": 50, "quote": "50-65 °C"},
        "bed_max": {"value": 65, "quote": "heated plate recommended around 65"},  # made-up quote -> unverified
        "density": {"value": 1.23, "quote": "1,23 g/cm³"},
        "weight_g": {"value": 1000, "quote": "1 kg"},
        "diameter": {"value": 17.5, "quote": "1.75mm"},          # out of range -> rejected
        "drying": {"value": "55°C for 6 hours", "quote": "Dry at 55°C for 6 hours"},
        "price": {"value": 21.99, "quote": "21.99 USD"},
        "max_print_speed": {"value": 300, "quote": "up to 300 mm/s"},  # invented -> unverified
    })
    base.update(over)
    return base


def test_check_and_form_mapping():
    src = extract.html_text(PAGE)
    f = extract.check(fake_answer(), src)
    assert f["nozzle_min"]["status"] == "verified"
    assert f["nozzle_max"]["status"] == "verified"
    assert f["density"]["status"] == "verified"
    assert f["bed_max"]["status"] == "unverified"
    assert f["max_print_speed"]["status"] == "unverified"
    assert f["diameter"]["status"] == "rejected"
    form = extract.to_filament_form(f)
    assert form["material"] == "PLA+"
    assert form["spec"] == {"nozzle_min": 205, "nozzle_max": 230, "bed_min": 50, "bed_max": 65, "density": 1.23}
    assert "diameter" not in form
    assert "Drying" in form["notes"]


def test_quote_must_contain_the_number():
    src = "Printing temperature 200-220C"
    f = extract.check({"nozzle_min": {"value": 190, "quote": "200-220C"}}, src)
    assert f["nozzle_min"]["status"] == "unverified"


def test_photo_values_are_unverified():
    f = extract.check({"nozzle_min": {"value": 200, "quote": "200"}}, None)
    assert f["nozzle_min"]["status"] == "unverified"


def test_min_max_swapped():
    f = extract.check({"nozzle_min": {"value": 230, "quote": "230"}, "nozzle_max": {"value": 200, "quote": "200"}},
                      "200 230")
    assert f["nozzle_min"]["value"] == 200 and f["nozzle_max"]["value"] == 230


@pytest.mark.parametrize("text,mat", [
    ("PLA+", "PLA+"), ("Silk PLA", "PLA Silk"), ("PETG-CF", "PETG-CF"), ("Nylon PA12-CF", "PA-CF"),
    ("TPU 95A", "TPU"), ("PLA Matte", "PLA"), ("ASA", "ASA"), ("polycarbonate", "PC"),
])
def test_guess_material(text, mat):
    assert extract.guess_material(text) == mat


def test_blocks_local_urls():
    for u in ("http://127.0.0.1:8765/api/backup", "http://localhost/", "file:///etc/passwd", "ftp://x"):
        with pytest.raises(extract.FetchError):
            extract.fetch_url(u)


def test_shrink_keeps_spec_lines():
    filler = "\n".join(f"lorem ipsum line {i} " * 5 for i in range(2000))
    t = extract.shrink(filler + "\nNozzle temperature: 210 °C\n" + filler, limit=4000)
    assert "Nozzle temperature: 210 °C" in t and len(t) <= 4000


def test_extract_pipeline_with_fake_model():
    seen = {}

    def chat(messages, schema):
        seen["schema"] = schema
        seen["prompt"] = messages[1]["content"]
        return json.dumps(fake_answer())

    res = extract.extract(chat, text=extract.html_text(PAGE))
    assert "Find these settings" in seen["prompt"] and "205–230℃" in seen["prompt"]
    assert seen["schema"]["properties"]["nozzle_min"]["properties"]["value"]["type"] == ["number", "null"]
    assert res["form"]["spec"]["nozzle_max"] == 230

    res = extract.extract(lambda m, s: json.dumps(fake_answer()), file_bytes=b"\x89PNG....", filename="label.png")
    assert res["kind"] == "image"
    assert all(v["status"] != "verified" for v in res["fields"].values() if v["status"] != "rejected")


@pytest.mark.parametrize("osname,plat,machine,gpu,expected", [
    ("nt", "win32", "AMD64", "nvidia", ["ollama-windows-amd64.zip"]),
    ("nt", "win32", "AMD64", "amd", ["ollama-windows-amd64.zip", "ollama-windows-amd64-rocm.zip"]),
    ("posix", "linux", "x86_64", "cpu", ["ollama-linux-amd64.tar.zst"]),
    ("posix", "darwin", "arm64", "cpu", ["ollama-darwin.tgz"]),
])
def test_release_assets(monkeypatch, osname, plat, machine, gpu, expected):
    monkeypatch.setattr(runtime.os, "name", osname)
    monkeypatch.setattr(runtime.sys, "platform", plat)
    monkeypatch.setattr(runtime.platform, "machine", lambda: machine)
    assert runtime.release_assets(gpu) == expected


def test_extract_rejects_path_traversal(tmp_path):
    import zipfile
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(RuntimeError):
        runtime._extract(z, tmp_path / "out")


def test_ai_endpoints(tmp_path, monkeypatch):
    from forge import app as app_mod
    monkeypatch.setattr(app_mod, "DATA_DIR", tmp_path)
    app = app_mod.create_app(tmp_path / "t.db", manage_ai=False)
    c = TestClient(app)
    s = c.get("/api/ai/status").json()
    assert s["settings"]["enabled"] is False and s["state"] == "off"
    r = c.post("/api/ai/extract", data={"text": "PLA 200-220C"})
    assert r.status_code == 409  # not set up yet

    s = c.put("/api/ai/settings", json={"model": "llama3.1:8b", "mode": "external"}).json()
    assert s["settings"]["model"] == "llama3.1:8b"
    assert c.put("/api/ai/settings", json={"model": "bad name; rm -rf"}).status_code == 400
    # settings persist across restarts
    app2 = app_mod.create_app(tmp_path / "t.db", manage_ai=False)
    assert app2.state.ai.settings["model"] == "llama3.1:8b"

    # pretend it's ready and use a fake model
    ai = app.state.ai
    monkeypatch.setattr(ai, "ready", lambda: True)
    monkeypatch.setattr(ai, "chat", lambda m, s: json.dumps(fake_answer()))
    r = c.post("/api/ai/extract", files={"file": ("page.html", PAGE.encode(), "text/html")})
    assert r.status_code == 200, r.text
    assert r.json()["form"]["spec"]["nozzle_min"] == 205
    assert c.post("/api/ai/extract", data={"url": "http://127.0.0.1/"}).status_code == 400


FAKE_OLLAMA = r'''#!PYTHON
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
host, port = os.environ["OLLAMA_HOST"].split(":")
models_dir = os.environ["OLLAMA_MODELS"]
os.makedirs(models_dir, exist_ok=True)
have = os.path.join(models_dir, "pulled")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/api/version": return self._json({"version": "fake"})
        if self.path == "/api/tags":
            return self._json({"models": [{"name": "m:1"}] if os.path.exists(have) else []})
        self.send_response(404); self.end_headers()
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/pull":
            self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
            for done in (0, 50, 100):
                self.wfile.write((json.dumps({"status": "pulling", "total": 100, "completed": done}) + "\n").encode())
            self.wfile.write(b'{"status":"success"}\n'); open(have, "w").close(); return
        if self.path == "/api/chat":
            return self._json({"message": {"content": json.dumps({"echo": body["format"] is not None})}})
HTTPServer((host, int(port)), H).serve_forever()
'''


@pytest.mark.skipif(__import__("os").name == "nt", reason="the fake Ollama is a shebang script")
def test_manager_lifecycle_with_fake_ollama(tmp_path):
    import socket
    import sys
    import time as _t
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    bindir = tmp_path / "ollama" / "bin"
    bindir.mkdir(parents=True)
    exe = bindir / "ollama"
    exe.write_text(FAKE_OLLAMA.replace("PYTHON", sys.executable))
    exe.chmod(0o755)

    m = runtime.OllamaManager(tmp_path, {"enabled": True, "model": "m:1", "port": port})
    m.start_async()
    for _ in range(100):
        if m.snapshot()["state"] in ("ready", "error"):
            break
        _t.sleep(0.1)
    snap = m.snapshot()
    assert snap["state"] == "ready", snap
    assert m.ready()
    assert json.loads(m.chat([{"role": "user", "content": "hi"}], {"type": "object"})) == {"echo": True}
    pid = m.proc.pid
    assert (tmp_path / "ollama" / "ollama.pid").read_text() == str(pid)
    m.stop()
    assert not m.is_up()
    assert not (tmp_path / "ollama" / "ollama.pid").exists()
    import os
    with pytest.raises(OSError):
        os.kill(pid, 0) if os.waitpid(pid, os.WNOHANG) == (0, 0) else (_ for _ in ()).throw(OSError())

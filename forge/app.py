"""Filament Forge web app (runs locally; open http://127.0.0.1:8765)."""


import io
import re
import sqlite3
import json
import os
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import calibration, klipper, materials, orca, paths, swatch, troubleshoot, tuning
from .ai import extract as ai_extract
from .ai.images import shrink as shrink_image
from .ai.runtime import CONTEXT_CHOICES, OllamaManager
from .db import DB, SCHEMA as SCHEMA_SQL
from .fields import FIELDS

ROOT = Path(__file__).resolve().parent
DATA_DIR = paths.default_data_dir()
PHOTO_DIR = DATA_DIR / "photos"
MAX_UPLOAD = 20_000_000
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}

# Starting point for the Snapmaker U1, taken from OrcaSlicer's own U1 machine
# profile. Import your printer.cfg to fill in temperature limits.
SNAPMAKER_U1 = {
    "name": "Snapmaker U1 (0.4 nozzle)",
    "data": {
        "orca_printer": "Snapmaker U1 (0.4 nozzle)",
        "kinematics": "corexy",
        "max_velocity": 500,
        "max_accel": 20000,
        "bed_x": 270, "bed_y": 270, "bed_z": 270,
        "nozzle_diameter": 0.4,
        "toolheads": 4,
        "nozzle_max_temp": None,
        "bed_max_temp": None,
        "hotend_max_flow": None,
        "notes": "Speeds/size from OrcaSlicer's U1 profile. Import printer.cfg for temperature limits; "
                 "set hotend max flow after a max-flow calibration.",
    },
}


def create_app(db_path: str | Path | None = None, manage_ai: bool = True) -> FastAPI:
    db = DB(db_path or DATA_DIR / "forge.db")
    ai = OllamaManager(DATA_DIR, db.get_setting("ai", {}))

    @asynccontextmanager
    async def lifespan(_app):
        # The AI engine only runs while this app runs.
        if manage_ai:
            ai.cleanup_stale()
            if ai.settings["enabled"]:
                ai.start_async()
        try:
            yield
        finally:
            if manage_ai:
                ai.stop()

    app = FastAPI(title="Filament Forge", lifespan=lifespan)
    app.state.db = db
    app.state.ai = ai

    if not db.one("SELECT id FROM printers LIMIT 1"):
        db.insert("printers", {"name": SNAPMAKER_U1["name"], "data": SNAPMAKER_U1["data"]})

    # ---------------------------------------------------------------- helpers
    def printer_dict(printer_id):
        if not printer_id:
            return None
        p = db.one("SELECT * FROM printers WHERE id = ?", (printer_id,))
        if not p:
            return None
        return {"id": p["id"], "name": p["name"], **p["data"]}

    def get_profile(pid: int) -> dict:
        p = db.one("SELECT * FROM profiles WHERE id = ?", (pid,))
        if not p:
            raise HTTPException(404, "Profile not found")
        return p

    def get_filament(fid: int) -> dict:
        f = db.one("SELECT * FROM filaments WHERE id = ?", (fid,))
        if not f:
            raise HTTPException(404, "Filament not found")
        return f

    def compat_for(profile: dict) -> list[str] | None:
        # Always name the exact Orca printer preset. Without this, Orca guesses
        # the printer from the text after "@" in the preset name ("Snapmaker U1"),
        # which doesn't match "Snapmaker U1 (0.4 nozzle)", and hides the preset.
        pr = printer_dict(profile.get("printer_id"))
        name = (pr or {}).get("orca_printer")
        return [name] if name else None

    def orca_group() -> dict:
        return {**orca.DEFAULT_GROUP, **(db.get_setting("orca_group") or {})}

    def preset_for(profile: dict, in_bundle: bool = False) -> dict:
        """The Orca preset for a profile, named and grouped under your group (VlottizFF).

        Inside a bundle the bundle itself carries the group name, so the
        preset name isn't prefixed again.
        """
        group = orca_group()
        settings = dict(profile["settings"])
        if group.get("as_vendor") and group.get("name", "").strip():
            settings["filament_vendor"] = group["name"].strip()
        name = profile["name"] if in_bundle else orca.group_name(profile["name"], group)
        return orca.to_preset(
            name, settings, material=profile.get("material"),
            inherits=profile.get("inherits"), compatible_printers=compat_for(profile),
            notes=orca.notes_for(profile["settings"], profile.get("sources") or {}),
        )

    # ------------------------------------------------------------------- meta
    @app.get("/api/meta")
    def meta():
        root = orca.config_dir()
        return {
            "materials": [{"name": m, "info": materials.material_info(m),
                           "orca_parent": orca.ORCA_PARENT.get(m, "")}
                          for m in materials.material_names()],
            "fields": [{"key": k, "label": l, "unit": u, "group": g, "step": s}
                       for k, l, u, g, s in FIELDS],
            "tests": calibration.tests_for_ui(),
            "orca_config_dir": str(root) if root else None,
            "data_dir": str(DATA_DIR),
            "orca_config_exists": bool(root and root.is_dir()),
            "orca_filament_dirs": [str(d) for d in orca.install_dirs()],
            "orca_active_dir": str(orca.active_user_dir() or ""),
            "source_confidence": tuning.SOURCE_CONFIDENCE,
        }

    # --------------------------------------------------------------- printers
    class PrinterIn(BaseModel):
        name: str
        data: dict = {}

    @app.get("/api/printers")
    def list_printers():
        return db.all("SELECT * FROM printers ORDER BY id")

    @app.post("/api/printers")
    def add_printer(body: PrinterIn):
        pid = db.insert("printers", {"name": body.name, "data": body.data})
        return db.one("SELECT * FROM printers WHERE id = ?", (pid,))

    @app.put("/api/printers/{pid}")
    def edit_printer(pid: int, body: PrinterIn):
        if not db.one("SELECT id FROM printers WHERE id = ?", (pid,)):
            raise HTTPException(404, "Printer not found")
        db.update("printers", pid, {"name": body.name, "data": body.data})
        return db.one("SELECT * FROM printers WHERE id = ?", (pid,))

    @app.delete("/api/printers/{pid}")
    def delete_printer(pid: int):
        db.delete("printers", pid)
        return {"ok": True}

    @app.post("/api/klipper/parse")
    async def parse_klipper(files: list[UploadFile] = File(...)):
        texts = []
        for f in files:
            raw = await f.read()
            if len(raw) > 2_000_000:
                raise HTTPException(400, f"{f.filename} is too large to be a Klipper config")
            texts.append(raw.decode("utf-8", errors="replace"))
        summary = klipper.summarize(texts)
        return {"summary": summary, "fields": klipper.to_printer_fields(summary)}

    # -------------------------------------------------------------- filaments
    class FilamentIn(BaseModel):
        brand: str | None = None
        name: str
        material: str
        colour_name: str | None = None
        colour_hex: str | None = None
        diameter: float | None = 1.75
        weight_g: float | None = None
        price: float | None = None
        currency: str | None = None
        purchased_on: str | None = None
        store: str | None = None
        url: str | None = None
        spools: int | None = 1
        spools_left: int | None = None
        notes: str | None = None
        spec: dict = {}

    @app.get("/api/filaments")
    def list_filaments():
        rows = db.all("SELECT * FROM filaments ORDER BY created DESC")
        counts = {r["filament_id"]: r["n"] for r in db.all(
            "SELECT filament_id, COUNT(*) AS n FROM profiles GROUP BY filament_id")}
        for r in rows:
            r["profile_count"] = counts.get(r["id"], 0)
        return rows

    @app.post("/api/filaments")
    def add_filament(body: FilamentIn):
        if body.material not in materials.MATERIALS:
            raise HTTPException(400, f"Unknown material {body.material}")
        fid = db.insert("filaments", body.model_dump())
        return get_filament(fid)

    @app.put("/api/filaments/{fid}")
    def edit_filament(fid: int, body: FilamentIn):
        get_filament(fid)
        db.update("filaments", fid, body.model_dump())
        return get_filament(fid)

    @app.delete("/api/filaments/{fid}")
    def delete_filament(fid: int):
        db.delete("filaments", fid)
        return {"ok": True}

    # --------------------------------------------------------------- profiles
    class ProfileCreate(BaseModel):
        filament_id: int
        printer_id: int | None = None
        name: str | None = None

    class ProfileEdit(BaseModel):
        name: str | None = None
        inherits: str | None = None
        printer_id: int | None = None
        settings: dict | None = None  # only changed keys; null value removes

    def _cost_per_kg(f: dict):
        if f.get("price") and f.get("weight_g"):
            return round(f["price"] / f["weight_g"] * 1000, 2)
        return None

    def _default_name(f: dict, printer: dict | None) -> str:
        parts = [f.get("brand"), f.get("name")]
        if f.get("colour_name") and f["colour_name"].lower() not in (f.get("name") or "").lower():
            parts.append(f["colour_name"])
        name = " ".join(p for p in parts if p)
        if printer:
            short = printer["name"].split("(")[0].strip()
            name += f" @{short}"
        return name

    def _spec_with_diameter(f: dict) -> dict:
        spec = dict(f.get("spec") or {})
        if f.get("diameter") and not spec.get("diameter"):
            spec["diameter"] = f["diameter"]
        return spec

    @app.get("/api/profiles")
    def list_profiles():
        rows = db.all("""
            SELECT p.*, f.brand, f.name AS filament_name, f.colour_hex, pr.name AS printer_name
            FROM profiles p
            LEFT JOIN filaments f ON f.id = p.filament_id
            LEFT JOIN printers pr ON pr.id = p.printer_id
            ORDER BY p.updated DESC""")
        for r in rows:
            r["confidence"] = tuning.confidence(r["settings"], r["sources"])["overall"]
            r.pop("settings"), r.pop("sources")
        return rows

    @app.post("/api/profiles")
    def create_profile(body: ProfileCreate):
        f = get_filament(body.filament_id)
        printer = printer_dict(body.printer_id)
        built = tuning.build(
            f["material"], spec=_spec_with_diameter(f), printer=printer,
            vendor=f.get("brand"), colour=f.get("colour_hex"), cost_per_kg=_cost_per_kg(f),
        )
        now = time.time()
        pid = db.insert("profiles", {
            "filament_id": f["id"], "printer_id": body.printer_id,
            "name": body.name or _default_name(f, printer),
            "material": f["material"], "inherits": orca.ORCA_PARENT.get(f["material"], ""),
            "settings": built["settings"], "sources": built["sources"], "notes": built["notes"],
            "created": now, "updated": now,
        })
        db.save_profile_version(pid, "Created")
        return profile_detail(pid)

    @app.get("/api/profiles/{pid}")
    def profile_detail(pid: int):
        p = get_profile(pid)
        printer = printer_dict(p.get("printer_id"))
        p["printer"] = printer
        p["filament"] = db.one("SELECT * FROM filaments WHERE id = ?", (p["filament_id"],)) if p.get("filament_id") else None
        p["confidence"] = tuning.confidence(p["settings"], p["sources"])
        p["speed"] = tuning.speed_hint(p["settings"], printer)
        p["versions"] = db.all(
            "SELECT id, reason, created FROM profile_versions WHERE profile_id = ? ORDER BY id DESC", (pid,))
        p["troubleshoots"] = [_session_view(t) for t in db.all(
            "SELECT * FROM troubleshoots WHERE profile_id = ? OR trial_profile_id = ? ORDER BY id DESC", (pid, pid))]
        if p.get("trial_of"):
            orig = db.one("SELECT id, name FROM profiles WHERE id = ?", (p["trial_of"],))
            sess = db.one("SELECT * FROM troubleshoots WHERE trial_profile_id = ? ORDER BY id DESC LIMIT 1", (pid,))
            p["trial"] = {"original": orig, "session": _session_view(sess) if sess else None}
        p["calibrations"] = db.all(
            "SELECT * FROM calibrations WHERE profile_id = ? ORDER BY id DESC", (pid,))
        p["orca_name"] = preset_for(p)["name"]
        p["exports"] = db.all(
            "SELECT id, preset_name, target, created FROM exports WHERE profile_id = ? ORDER BY id DESC", (pid,))
        return p

    @app.put("/api/profiles/{pid}")
    def edit_profile(pid: int, body: ProfileEdit):
        p = get_profile(pid)
        meta_changes = {}
        if body.name is not None and body.name.strip():
            meta_changes["name"] = body.name.strip()
        if body.inherits is not None:
            meta_changes["inherits"] = body.inherits.strip()
        if body.printer_id is not None:
            meta_changes["printer_id"] = body.printer_id or None
        if meta_changes:
            meta_changes["updated"] = time.time()
            db.update("profiles", pid, meta_changes)
        if body.settings:
            settings, sources = dict(p["settings"]), dict(p["sources"])
            changed = []
            for k, v in body.settings.items():
                if v is None or v == "":
                    if k in settings:
                        settings.pop(k)
                        sources.pop(k, None)
                        changed.append(k)
                elif settings.get(k) != v:
                    settings[k] = v
                    sources[k] = "manual"
                    changed.append(k)
            if changed:
                db.set_profile_settings(pid, settings, sources, "Edited: " + ", ".join(changed))
        return profile_detail(pid)

    class ConfirmIn(BaseModel):
        keys: list[str]

    @app.post("/api/profiles/{pid}/confirm")
    def confirm_settings(pid: int, body: ConfirmIn):
        """Mark settings as proven in a real print (counts as 100% confident)."""
        p = get_profile(pid)
        settings, sources = p["settings"], dict(p["sources"])
        done = []
        for k in body.keys:
            for key in tuning.CONFIRM_GROUPS.get(k, [k]):
                if key in settings and sources.get(key) not in ("calibrated", "tested"):
                    sources[key] = "tested"
                    done.append(key)
        if done:
            db.set_profile_settings(pid, settings, sources, "Confirmed in a print: " + ", ".join(done))
        return profile_detail(pid)

    # ------------------------------------------------------ issue detector
    def _save_photos(pid: int, uploads) -> tuple[list[str], list[bytes]]:
        names, blobs = [], []
        for ph in uploads or []:
            if not ph.filename:
                continue
            ext = Path(ph.filename).suffix.lower()
            if ext not in PHOTO_EXT:
                raise HTTPException(400, f"{ph.filename}: only image files can be attached")
            data = ph.file.read(15_000_001)
            if len(data) > 15_000_000:
                raise HTTPException(400, f"{ph.filename} is over 15 MB")
            PHOTO_DIR.mkdir(parents=True, exist_ok=True)
            name = f"{pid}-issue-{int(time.time())}-{secrets.token_hex(4)}{ext}"
            (PHOTO_DIR / name).write_bytes(data)
            names.append(name)
            blobs.append(data)
        return names, [shrink_image(b) for b in blobs[:4]]

    def _session_view(sess: dict) -> dict:
        diag = sess["diagnosis"] or {}
        for it in diag.get("issues", []):
            info = troubleshoot.ISSUES.get(it["id"], {})
            it["title"], it["tips"], it["looks_like"] = info.get("title"), info.get("tips", []), info.get("looks_like")
        return sess

    def _troubleshoot_context(pid: int) -> dict:
        """Things the suggestions can use, e.g. the speed your VFA test found smoothest."""
        ctx = {}
        for c in db.all("SELECT * FROM calibrations WHERE profile_id = ? AND test = 'vfa' ORDER BY id DESC", (pid,)):
            try:
                ctx["vfa_speed"] = calibration.compute("vfa", c["inputs"], {})["extra"]["_speed"]
                break
            except (ValueError, KeyError):
                continue
        return ctx

    class ReplanIn(BaseModel):
        issues: list[str]

    @app.post("/api/troubleshoots/{sid}/replan")
    def replan(sid: int, body: ReplanIn):
        """You untick wrong guesses (or add the right problem); the suggestions are worked out again."""
        sess = db.one("SELECT * FROM troubleshoots WHERE id = ?", (sid,))
        if not sess:
            raise HTTPException(404, "Not found")
        if sess.get("trial_profile_id") and db.one("SELECT id FROM profiles WHERE id = ?", (sess["trial_profile_id"],)):
            raise HTTPException(400, "A trial was already made from this report; delete it first or start a new report")
        p = get_profile(sess["profile_id"])
        ids = [i for i in dict.fromkeys(body.issues) if i in troubleshoot.ISSUES]
        diag = sess["diagnosis"] or {}
        old = {i["id"]: i for i in diag.get("issues", [])}
        diag["issues"] = [old.get(i) or {"id": i, "confidence": None, "evidence": "You picked this."} for i in ids]
        for i in diag["issues"]:
            for k in ("title", "tips", "looks_like"):
                i.pop(k, None)
        diag["process"] = troubleshoot.process_suggestions(ids, _troubleshoot_context(p["id"]))
        changes = troubleshoot.plan_changes(ids, p["settings"], printer_dict(p.get("printer_id")))
        db.update("troubleshoots", sid, {"diagnosis": diag, "changes": changes, "updated": time.time()})
        return _session_view(db.one("SELECT * FROM troubleshoots WHERE id = ?", (sid,)))

    @app.get("/api/issues")
    def issue_list():
        return troubleshoot.catalogue()

    @app.post("/api/profiles/{pid}/diagnose")
    def diagnose(pid: int, description: str = Form(""), issues: str = Form(""),
                 use_ai: bool = Form(True), photos: list[UploadFile] | None = File(None)):
        """Find what's wrong (AI from a photo, or your own pick) and plan setting changes."""
        p = get_profile(pid)
        manual = [i for i in (x.strip() for x in issues.split(",")) if i in troubleshoot.ISSUES]
        if not manual and not use_ai:
            raise HTTPException(400, "Pick at least one problem")
        names, blobs = _save_photos(pid, photos)
        if manual:
            diag = {"issues": [{"id": i, "confidence": None, "evidence": "You picked this."} for i in manual],
                    "summary": "", "by": "you"}
        else:
            if not blobs and not description.strip():
                raise HTTPException(400, "Add a photo or describe the problem")
            if not ai.ready():
                raise HTTPException(409, "The AI isn't running. Start it in the AI tab, or pick the problem yourself.")
            try:
                diag = troubleshoot.diagnose_with_ai(ai.chat, description, blobs, p.get("material"), p["settings"])
            except Exception as e:
                raise HTTPException(502, f"AI step failed: {e}")
            diag["by"] = "ai"
        if diag["by"] == "ai":
            diag["issues"] = troubleshoot.combine(diag["issues"], description)
        ids = [i["id"] for i in diag["issues"]]
        changes = troubleshoot.plan_changes(ids, p["settings"], printer_dict(p.get("printer_id")))
        diag["process"] = troubleshoot.process_suggestions(ids, _troubleshoot_context(pid))
        sid = db.insert("troubleshoots", {"profile_id": pid, "description": description.strip()[:1000],
                                          "photos": names, "diagnosis": diag, "changes": changes,
                                          "status": "diagnosed", "updated": time.time()})
        return _session_view(db.one("SELECT * FROM troubleshoots WHERE id = ?", (sid,)))

    @app.post("/api/troubleshoots/{sid}/trial")
    def make_trial(sid: int):
        """Copy the profile with the suggested changes, to print and compare."""
        sess = db.one("SELECT * FROM troubleshoots WHERE id = ?", (sid,))
        if not sess:
            raise HTTPException(404, "Not found")
        if not sess["changes"]:
            raise HTTPException(400, "There are no setting changes to try for this problem")
        if sess.get("trial_profile_id") and db.one("SELECT id FROM profiles WHERE id = ?", (sess["trial_profile_id"],)):
            return profile_detail(sess["trial_profile_id"])
        p = get_profile(sess["profile_id"])
        settings, sources = dict(p["settings"]), dict(p["sources"])
        for c in sess["changes"]:
            settings[c["key"]] = c["new"]
            sources[c["key"]] = "manual"
        first = (sess["diagnosis"].get("issues") or [{}])[0].get("id", "fix")
        label = troubleshoot.ISSUES.get(first, {}).get("title", "fix").split(" /")[0].lower()
        now = time.time()
        tid = db.insert("profiles", {
            "filament_id": p["filament_id"], "printer_id": p["printer_id"], "trial_of": p["id"],
            "name": f"{p['name']} (trial: {label})", "material": p["material"], "inherits": p["inherits"],
            "settings": settings, "sources": sources,
            "notes": [f"Trial copy of '{p['name']}' to fix: {label}. Print with it, then say if it's better."],
            "created": now, "updated": now,
        })
        db.save_profile_version(tid, "Trial created: " + ", ".join(f"{c['key']} {c['old']} -> {c['new']}"
                                                                    for c in sess["changes"]))
        db.update("troubleshoots", sid, {"trial_profile_id": tid, "status": "trial", "updated": now})
        return profile_detail(tid)

    class TrialResult(BaseModel):
        result: str  # better | not_better | keep

    @app.post("/api/profiles/{tid}/trial/result")
    def trial_result(tid: int, body: TrialResult):
        """better: copy the changes into the original (proven in a print) and remove the trial.
        not_better: remove the trial. keep: turn the trial into a normal profile."""
        t = get_profile(tid)
        if not t.get("trial_of"):
            raise HTTPException(400, "That isn't a trial profile")
        sess = db.one("SELECT * FROM troubleshoots WHERE trial_profile_id = ? ORDER BY id DESC LIMIT 1", (tid,))
        orig = db.one("SELECT * FROM profiles WHERE id = ?", (t["trial_of"],))
        now = time.time()
        if body.result == "better":
            if not orig:
                raise HTTPException(400, "The original profile was deleted; choose 'keep' instead")
            settings, sources = dict(orig["settings"]), dict(orig["sources"])
            keys = [c["key"] for c in (sess or {}).get("changes", [])] or \
                   [k for k, v in t["settings"].items() if orig["settings"].get(k) != v]
            for k in keys:
                if k in t["settings"]:
                    settings[k] = t["settings"][k]
                    sources[k] = "tested"
            db.set_profile_settings(orig["id"], settings, sources, "Fix confirmed in a print: " + ", ".join(keys))
            db.delete("profiles", tid)
            if sess:
                db.update("troubleshoots", sess["id"], {"status": "fixed", "updated": now})
            return profile_detail(orig["id"])
        if body.result == "not_better":
            db.delete("profiles", tid)
            if sess:
                db.update("troubleshoots", sess["id"], {"status": "not_better", "updated": now})
            return profile_detail(orig["id"]) if orig else {"ok": True}
        if body.result == "keep":
            db.update("profiles", tid, {"trial_of": None, "name": t["name"].replace(" (trial: ", " (").strip(),
                                        "updated": now})
            if sess:
                db.update("troubleshoots", sess["id"], {"status": "kept", "updated": now})
            return profile_detail(tid)
        raise HTTPException(400, "result must be better, not_better or keep")

    @app.post("/api/profiles/{pid}/rebuild")
    def rebuild_profile(pid: int):
        """Recompute from material/spec/printer, keeping manual + calibrated values."""
        p = get_profile(pid)
        f = db.one("SELECT * FROM filaments WHERE id = ?", (p["filament_id"],)) if p.get("filament_id") else None
        material = (f or {}).get("material") or p.get("material")
        if material not in materials.MATERIALS:
            raise HTTPException(400, "This profile has no known material to rebuild from")
        built = tuning.build(
            material, spec=_spec_with_diameter(f or {}), printer=printer_dict(p.get("printer_id")),
            vendor=(f or {}).get("brand"), colour=(f or {}).get("colour_hex"),
            cost_per_kg=_cost_per_kg(f or {}),
        )
        settings, sources = built["settings"], built["sources"]
        for k, src in p["sources"].items():
            if src in ("manual", "calibrated", "tested", "imported") and k in p["settings"]:
                settings[k] = p["settings"][k]
                sources[k] = src
        db.update("profiles", pid, {"notes": built["notes"]})
        db.set_profile_settings(pid, settings, sources, "Rebuilt from printer/spec")
        return profile_detail(pid)

    @app.post("/api/profiles/{pid}/restore/{vid}")
    def restore_version(pid: int, vid: int):
        v = db.one("SELECT * FROM profile_versions WHERE id = ? AND profile_id = ?", (vid, pid))
        if not v:
            raise HTTPException(404, "Version not found")
        db.set_profile_settings(pid, v["settings"], v["sources"], f"Restored version #{vid}")
        return profile_detail(pid)

    @app.post("/api/profiles/{pid}/duplicate")
    def duplicate_profile(pid: int):
        p = get_profile(pid)
        now = time.time()
        new_id = db.insert("profiles", {
            "filament_id": p["filament_id"], "printer_id": p["printer_id"],
            "name": p["name"] + " (copy)", "material": p["material"], "inherits": p["inherits"],
            "settings": p["settings"], "sources": p["sources"], "notes": p["notes"],
            "created": now, "updated": now,
        })
        db.save_profile_version(new_id, f"Copied from profile #{pid}")
        return profile_detail(new_id)

    @app.delete("/api/profiles/{pid}")
    def delete_profile(pid: int):
        db.delete("profiles", pid)
        return {"ok": True}

    # ----------------------------------------------------------------- export
    @app.get("/api/profiles/{pid}/preview")
    def preview_export(pid: int):
        return preset_for(get_profile(pid))

    @app.get("/api/profiles/{pid}/export")
    def export_profile(pid: int):
        p = get_profile(pid)
        preset = preset_for(p)
        content = json.dumps(preset, indent=4)
        db.insert("exports", {"profile_id": pid, "preset_name": p["name"],
                              "content": content, "target": "download"})
        fname = orca.safe_filename(p["name"]) + ".json"
        return Response(content, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    class InstallIn(BaseModel):
        target_dir: str

    def _orca_copies(p: dict) -> list[dict]:
        return orca.find_copies(orca.name_variants(p["name"], orca_group()))

    def _snapshot_file(path: str, name: str) -> None:
        """Keep a copy in Orca snapshots before the app changes or removes an Orca file."""
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError:
            return
        last = db.one("SELECT content FROM orca_snapshots WHERE path = ? ORDER BY id DESC LIMIT 1", (path,))
        if not last or last["content"] != content:
            db.insert("orca_snapshots", {"path": path, "name": name, "content": content})

    @app.get("/api/profiles/{pid}/orca-status")
    def orca_status(pid: int):
        """Is this profile in Orca, and is that copy the same as the profile now?"""
        p = get_profile(pid)
        want = preset_for(p)
        copies = [{"path": c["path"], "name": c["name"], "kind": c["kind"], "account": c["account"],
                   "up_to_date": c["content"] == want} for c in _orca_copies(p)]
        return {"copies": copies, "installed": bool(copies),
                "up_to_date": any(c["up_to_date"] and c["kind"] == "user" for c in copies) and len(copies) == 1,
                "orca_running": orca.orca_running()}

    @app.post("/api/profiles/{pid}/install")
    def install_profile(pid: int, body: InstallIn):
        """Install (or update) this profile in Orca, keeping exactly one copy there.

        Older copies of the same profile (e.g. under its name before the group
        prefix, or from an imported bundle) are backed up to the Orca snapshots
        and removed, so it never shows up twice.
        """
        allowed = {str(d) for d in orca.install_dirs()}
        if body.target_dir not in allowed:
            raise HTTPException(400, "That folder isn't an OrcaSlicer user filament folder.")
        p = get_profile(pid)
        preset = preset_for(p)
        target = Path(body.target_dir) / (orca.safe_filename(preset["name"]) + ".json")
        account = Path(body.target_dir).parent.name
        copies = [c for c in _orca_copies(p) if c["account"] == account]  # other Orca accounts are left alone
        if len(copies) == 1 and Path(copies[0]["path"]) == target and copies[0]["content"] == preset:
            return {"path": str(target), "status": "up_to_date", "removed": [],
                    "message": "Already in Orca and up to date. Nothing to do."}
        removed = []
        for c in copies:
            _snapshot_file(c["path"], c["name"])
            if Path(c["path"]) != target:
                orca.remove_copy(Path(c["path"]))
                removed.append({"name": c["name"], "kind": c["kind"], "path": c["path"]})
        updated = target.exists()
        path = orca.install(preset, Path(body.target_dir))
        db.insert("exports", {"profile_id": pid, "preset_name": p["name"],
                              "content": json.dumps(preset, indent=4), "target": str(path)})
        msg = ("Updated in Orca." if updated else "Installed.") + \
            (f" Removed {len(removed)} older cop{'y' if len(removed) == 1 else 'ies'}." if removed else "") + \
            (" Restart OrcaSlicer to see it." if orca.orca_running() else " It shows up next time you open Orca.")
        return {"path": str(path), "status": "updated" if updated else "installed", "removed": removed, "message": msg}

    @app.post("/api/profiles/{pid}/uninstall")
    def uninstall_profile(pid: int):
        """Remove this profile from Orca (every copy). Each file is backed up to the Orca snapshots first."""
        p = get_profile(pid)
        removed = []
        for c in _orca_copies(p):
            _snapshot_file(c["path"], c["name"])
            orca.remove_copy(Path(c["path"]))
            removed.append({"name": c["name"], "kind": c["kind"], "path": c["path"]})
        if not removed:
            return {"removed": [], "message": "It isn't in Orca."}
        db.insert("exports", {"profile_id": pid, "preset_name": p["name"], "content": "",
                              "target": "removed from Orca"})
        return {"removed": removed, "message": f"Removed from Orca ({len(removed)} file{'s' if len(removed) > 1 else ''}; "
                "backed up in Orca snapshots)." + (" Restart OrcaSlicer." if orca.orca_running() else "")}

    @app.get("/api/export-bundle")
    def export_bundle():
        """All profiles as one Orca bundle, shown under your group name in Orca."""
        profiles = db.all("SELECT * FROM profiles ORDER BY name")
        if not profiles:
            raise HTTPException(400, "There are no profiles to export yet")
        name = orca_group().get("name", "").strip() or "Filament Forge"
        data = orca.make_bundle([preset_for(p, in_bundle=True) for p in profiles], name)
        for p in profiles:
            db.insert("exports", {"profile_id": p["id"], "preset_name": p["name"],
                                  "content": json.dumps(preset_for(p, in_bundle=True)), "target": f"bundle {name}"})
        fname = orca.safe_filename(name) + orca.BUNDLE_EXT
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    # -------------------------------------------------------------- QR swatch
    def qr_settings() -> dict:
        return {"base_url": swatch.DEFAULT_BASE_URL, **(db.get_setting("qr") or {})}

    def qr_link(p: dict) -> str:
        preset = preset_for(p, in_bundle=True)
        values = {k: preset[k][0] for k in orca.QR_FIELDS_V1 if k in preset}
        group = orca_group()
        payload = swatch.encode(
            p["name"], group["name"].strip() if group.get("prefix_names") else "",
            preset.get("inherits", ""), (preset.get("compatible_printers") or [""])[0],
            values, p.get("sources") or {})
        return swatch.link(qr_settings()["base_url"], payload)

    class QRSettingsIn(BaseModel):
        base_url: str

    @app.get("/api/settings/qr")
    def get_qr_settings():
        return qr_settings()

    @app.put("/api/settings/qr")
    def set_qr_settings(body: QRSettingsIn):
        url = body.base_url.strip()
        if not re.fullmatch(r"https?://[^\s#]+", url):
            raise HTTPException(400, "Enter a full web address starting with https://")
        db.set_setting("qr", {"base_url": url})
        return qr_settings()

    @app.get("/api/profiles/{pid}/qr")
    def profile_qr(pid: int, size: float = 60):
        url = qr_link(get_profile(pid))
        try:
            matrix = swatch.qr_matrix(url)
        except ValueError:
            raise HTTPException(400, "Too much data for a QR code")
        n = len(matrix)
        return {"url": url, "chars": len(url), "modules": n, "svg": swatch.qr_svg(url),
                "label": swatch.label_text(get_profile(pid)["name"]),
                "module_mm": round(size / (n + 2 * swatch.QUIET), 2)}

    @app.get("/api/profiles/{pid}/swatch.3mf")
    def profile_swatch(pid: int, style: str = "two_colour", size: float = 60, thickness: float = 5,
                       label: str | None = None):
        if style not in ("two_colour", "hollow"):
            raise HTTPException(400, "style must be two_colour or hollow")
        if not (30 <= size <= 150 and 2 <= thickness <= 15):
            raise HTTPException(400, "Size must be 30-150 mm and thickness 2-15 mm")
        p = get_profile(pid)
        text = swatch.label_text(p["name"]) if label is None else label[:80]
        parts, _cell = swatch.build_parts(swatch.qr_matrix(qr_link(p)), size, thickness, style, text)
        data = swatch.to_3mf(parts, f"{p['name']} QR swatch")
        fname = orca.safe_filename(f"{p['name']} QR swatch") + ".3mf"
        return Response(data, media_type="model/3mf",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    @app.get("/api/export-all")
    def export_all():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            used: set[str] = set()
            for p in db.all("SELECT * FROM profiles ORDER BY id"):
                base = orca.safe_filename(p["name"])
                fname, n = base + ".json", 2
                while fname.lower() in used:
                    fname, n = f"{base} ({n}).json", n + 1
                used.add(fname.lower())
                z.writestr(fname, json.dumps(preset_for(p), indent=4))
        stamp = time.strftime("%Y%m%d")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="filament-presets-{stamp}.zip"'})

    # ------------------------------------------------------------ calibration
    class CalPreview(BaseModel):
        test: str
        inputs: dict = {}

    @app.post("/api/profiles/{pid}/calibrations/preview")
    def calibration_preview(pid: int, body: CalPreview):
        p = get_profile(pid)
        try:
            return calibration.compute(body.test, body.inputs, p["settings"])
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/profiles/{pid}/calibrations")
    async def calibration_save(
        pid: int,
        test: str = Form(...),
        inputs: str = Form("{}"),
        notes: str = Form(""),
        apply: bool = Form(True),
        photos: list[UploadFile] | None = File(None),
    ):
        p = get_profile(pid)
        try:
            inp = json.loads(inputs or "{}")
            res = calibration.compute(test, inp, p["settings"])
        except (ValueError, json.JSONDecodeError) as e:
            raise HTTPException(400, str(e))

        saved_photos = []
        for ph in photos or []:
            if not ph.filename:
                continue
            ext = Path(ph.filename).suffix.lower()
            if ext not in PHOTO_EXT:
                raise HTTPException(400, f"{ph.filename}: only image files can be attached")
            data = await ph.read()
            if len(data) > 15_000_000:
                raise HTTPException(400, f"{ph.filename} is over 15 MB")
            PHOTO_DIR.mkdir(parents=True, exist_ok=True)
            name = f"{pid}-{int(time.time())}-{secrets.token_hex(4)}{ext}"
            (PHOTO_DIR / name).write_bytes(data)
            saved_photos.append(name)

        do_apply = apply and res["setting"] and res["new"] is not None
        if do_apply:
            settings, sources = dict(p["settings"]), dict(p["sources"])
            settings[res["setting"]] = res["new"]
            sources[res["setting"]] = "calibrated"
            for k, v in res["extra"].items():
                if not k.startswith("_"):
                    settings[k] = v
                    sources[k] = "calibrated"
            db.set_profile_settings(pid, settings, sources,
                                    f"Calibration: {calibration.TESTS[test]['title']}")
            if test == "max_flow" and p.get("printer_id"):
                pr = db.one("SELECT * FROM printers WHERE id = ?", (p["printer_id"],))
                measured = res["extra"].get("_measured")
                cur = pr["data"].get("hotend_max_flow")
                if measured and (not cur or measured > cur):
                    pr["data"]["hotend_max_flow"] = measured
                    db.update("printers", pr["id"], {"data": pr["data"]})

        db.insert("calibrations", {
            "profile_id": pid, "test": test, "inputs": inp,
            "setting": res["setting"],
            "old_value": None if res["old"] is None else str(res["old"]),
            "new_value": None if res["new"] is None else str(res["new"]),
            "applied": 1 if do_apply else 0, "notes": notes, "photos": saved_photos,
        })
        return profile_detail(pid)

    @app.delete("/api/calibrations/{cid}")
    def delete_calibration(cid: int, undo: bool = False):
        """Remove a calibration log entry, optionally undoing what it changed.

        Undo restores only the settings that calibration changed, and only
        where you haven't changed them again since.
        """
        c = db.one("SELECT * FROM calibrations WHERE id = ?", (cid,))
        if not c:
            raise HTTPException(404, "Calibration entry not found")
        reverted, kept = [], []
        if undo and c["applied"]:
            pid = c["profile_id"]
            # The version saved by this calibration, and the one just before it.
            after = db.one("""SELECT * FROM profile_versions WHERE profile_id = ? AND reason LIKE 'Calibration:%'
                              AND created <= ? ORDER BY id DESC LIMIT 1""", (pid, c["created"]))
            before = after and db.one("SELECT * FROM profile_versions WHERE profile_id = ? AND id < ? "
                                      "ORDER BY id DESC LIMIT 1", (pid, after["id"]))
            if after and before:
                p = get_profile(pid)
                settings, sources = dict(p["settings"]), dict(p["sources"])
                changed = {k for k in set(after["settings"]) | set(before["settings"])
                           if after["settings"].get(k) != before["settings"].get(k)}
                for k in sorted(changed):
                    if settings.get(k) != after["settings"].get(k):
                        kept.append(k)  # edited again since; leave it alone
                        continue
                    if k in before["settings"]:
                        settings[k] = before["settings"][k]
                        sources[k] = before["sources"].get(k, "baseline")
                    else:
                        settings.pop(k, None)
                        sources.pop(k, None)
                    reverted.append(k)
                if reverted:
                    title = calibration.TESTS.get(c["test"], {}).get("title", c["test"])
                    db.set_profile_settings(pid, settings, sources, f"Undid calibration: {title}")
        for ph in c["photos"] or []:
            path = (PHOTO_DIR / ph).resolve()
            if path.parent == PHOTO_DIR.resolve():
                path.unlink(missing_ok=True)
        db.delete("calibrations", cid)
        return {"ok": True, "reverted": reverted, "kept": kept}

    @app.get("/photos/{name}")
    def photo(name: str):
        path = (PHOTO_DIR / name).resolve()
        if path.parent != PHOTO_DIR.resolve() or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    # ------------------------------------------------- Orca backup / import
    @app.get("/api/orca/scan")
    def orca_scan():
        return {"presets": orca.scan(),
                "snapshots": db.all("SELECT id, path, name, created FROM orca_snapshots ORDER BY id DESC LIMIT 500")}

    @app.post("/api/orca/snapshot")
    def orca_snapshot():
        n = 0
        for item in orca.scan():
            try:
                content = Path(item["path"]).read_text(encoding="utf-8")
            except OSError:
                continue
            last = db.one("SELECT content FROM orca_snapshots WHERE path = ? ORDER BY id DESC LIMIT 1",
                          (item["path"],))
            if last and last["content"] == content:
                continue
            db.insert("orca_snapshots", {"path": item["path"], "name": item["name"], "content": content})
            n += 1
        return {"saved": n}

    @app.get("/api/orca/snapshots/{sid}")
    def orca_snapshot_file(sid: int):
        s = db.one("SELECT * FROM orca_snapshots WHERE id = ?", (sid,))
        if not s:
            raise HTTPException(404)
        fname = orca.safe_filename(s["name"] or "preset") + ".json"
        return Response(s["content"], media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    def _import_preset(data: dict, printer_id: int | None) -> dict:
        """Import an Orca filament preset as a filament (log entry) + profile.

        Re-importing a preset whose profile already exists updates that profile
        (with a new history version) instead of making a duplicate.
        """
        if not isinstance(data, dict) or data.get("type", "filament") != "filament":
            raise HTTPException(400, "That file isn't an OrcaSlicer filament preset")
        group = orca_group()
        parsed = orca.from_preset(data)
        if not parsed["settings"]:
            raise HTTPException(400, "That preset has no settings in it")
        info = orca.filament_from_preset(parsed["name"], parsed["settings"], group)
        profile_name = orca.strip_group(parsed["name"], group)

        # material: the name is often more specific than filament_type ("PLA Silk")
        ftype = parsed["material"]
        mat = ai_extract.guess_material(f"{info['name']} {ftype or ''}") or ftype
        if mat not in materials.MATERIALS:
            mat = next((m for m in materials.MATERIALS if mat and m.upper() == str(mat).upper()), mat)

        # printer: "@Snapmaker U1" in the name, or the preset's compatible printers
        if printer_id is None:
            hints = [info["printer_hint"], *(data.get("compatible_printers") or [])]
            for pr in db.all("SELECT * FROM printers ORDER BY id"):
                names = [pr["name"].lower(), str(pr["data"].get("orca_printer") or "").lower()]
                if any(h and any(n.startswith(h.lower()) or h.lower().startswith(n.split(" (")[0])
                                 for n in names if n) for h in hints):
                    printer_id = pr["id"]
                    break

        # filament log entry: reuse one with the same brand + name
        f = db.one("SELECT * FROM filaments WHERE lower(coalesce(brand,'')) = ? AND lower(name) = ? "
                   "AND lower(coalesce(colour_name,'')) = ?",
                   ((info["brand"] or "").lower(), info["name"].lower(), (info["colour_name"] or "").lower()))
        new_filament = f is None
        if new_filament:
            fid = db.insert("filaments", {
                "brand": info["brand"], "name": info["name"], "material": mat if mat in materials.MATERIALS else "PLA",
                "colour_name": info["colour_name"], "colour_hex": info["colour_hex"],
                "diameter": info["diameter"], "price": info["price"], "weight_g": info["weight_g"],
                "spec": info["spec"], "notes": "Imported from an OrcaSlicer preset.",
            })
        else:
            fid = f["id"]

        settings = parsed["settings"]
        if group.get("as_vendor") and settings.get("filament_vendor") == group.get("name") and info["brand"]:
            settings["filament_vendor"] = info["brand"]  # keep the real brand inside the app
        now = time.time()
        existing = db.one("SELECT * FROM profiles WHERE lower(name) = ? ORDER BY id LIMIT 1", (profile_name.lower(),))
        if existing:
            pid = existing["id"]
            sources = dict(existing["sources"])
            tagged = parsed["sources"]
            changed = [k for k, v in settings.items() if existing["settings"].get(k) != v]
            for k in changed:
                sources[k] = tagged.get(k, "imported")
            for k, src in tagged.items():  # a newer calibration recorded in the file
                if src in ("calibrated", "tested") and sources.get(k) not in ("calibrated", "tested") \
                        and existing["settings"].get(k) == settings.get(k):
                    sources[k] = src
                    changed.append(k)
            merged = {**existing["settings"], **settings}
            db.update("profiles", pid, {"filament_id": existing["filament_id"] or fid,
                                        "printer_id": existing["printer_id"] or printer_id, "updated": now})
            db.set_profile_settings(pid, merged, sources,
                                    "Re-imported from Orca" + (f": {', '.join(changed)}" if changed else " (no changes)"))
        else:
            changed = list(settings)
            pid = db.insert("profiles", {
                "filament_id": fid, "printer_id": printer_id,
                "name": profile_name, "material": mat, "inherits": parsed["inherits"],
                "settings": settings, "sources": {k: parsed["sources"].get(k, "imported") for k in settings},
                "notes": [f"Imported from OrcaSlicer. Anything not in the file comes from its parent preset "
                          f"('{parsed['inherits'] or 'none'}') in Orca."],
                "created": now, "updated": now,
            })
            db.save_profile_version(pid, "Imported from Orca")
        return {"profile_id": pid, "filament_id": fid, "new_filament": new_filament,
                "new_profile": not existing, "changed": changed if existing else [], "name": profile_name}

    class ImportPath(BaseModel):
        path: str
        printer_id: int | None = None

    @app.post("/api/orca/import-path")
    def orca_import_path(body: ImportPath):
        allowed = {p["path"] for p in orca.scan()}
        if body.path not in allowed:
            raise HTTPException(400, "Not a preset in Orca's user folder")
        data = json.loads(Path(body.path).read_text(encoding="utf-8"))
        r = _import_preset(data, body.printer_id)
        return {**profile_detail(r["profile_id"]), "import": r}

    @app.post("/api/orca/import-file")
    async def orca_import_file(files: list[UploadFile] = File(...), printer_id: int | None = Form(None)):
        """Import one or more Orca filament preset .json files (or a zip of them)."""
        results = []
        for file in files:
            raw = await file.read(MAX_UPLOAD + 1)
            if len(raw) > MAX_UPLOAD:
                results.append({"file": file.filename, "error": "File is too large"})
                continue
            items = []
            if raw[:2] == b"PK":
                try:
                    z = zipfile.ZipFile(io.BytesIO(raw))
                    items = [(n, z.read(n)) for n in z.namelist() if n.lower().endswith(".json")][:200]
                except zipfile.BadZipFile:
                    results.append({"file": file.filename, "error": "Damaged zip file"})
                    continue
            else:
                items = [(file.filename, raw)]
            for name, blob in items:
                try:
                    data = json.loads(blob.decode("utf-8-sig"))
                    results.append({"file": name, **_import_preset(data, printer_id)})
                except (ValueError, UnicodeDecodeError):
                    results.append({"file": name, "error": "Not a valid .json file"})
                except HTTPException as e:
                    results.append({"file": name, "error": e.detail})
        if not results:
            raise HTTPException(400, "No .json presets found")
        return {"results": results}

    class SwatchLinkIn(BaseModel):
        link: str

    @app.post("/api/import-swatch-link")
    def import_swatch_link(body: SwatchLinkIn):
        """Import a profile from a QR swatch link (what a phone shows when it scans one)."""
        text = body.link.strip()
        frag = text.split("#", 1)[1] if "#" in text else text
        try:
            d = swatch.decode(frag)
        except ValueError as e:
            raise HTTPException(400, f"{e}. Copy the whole link your phone shows after scanning.")
        preset = orca.to_preset(
            f"{d['group']} {d['name']}" if d["group"] else d["name"], d["settings"], inherits=d["inherits"],
            compatible_printers=[d["printer"]] if d["printer"] else None,
            notes=orca.notes_for(d["settings"], d["sources"]))
        r = _import_preset(preset, None)
        return {**profile_detail(r["profile_id"]), "import": r}

    @app.post("/api/scan-swatch")
    def scan_swatch(file: UploadFile = File(...), import_now: bool = Form(True)):
        """Read the QR code on a swatch from a photo or webcam frame, and import it."""
        data = file.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(400, "That image is over 20 MB")
        try:
            link = swatch.read_qr(data)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        if not link:
            raise HTTPException(422, "No Filament Forge QR code found. Hold the swatch flat, closer, in good light.")
        if not import_now:
            return {"link": link}
        return {**import_swatch_link(SwatchLinkIn(link=link)), "link": link}

    # ------------------------------------------------- your group in Orca
    class GroupIn(BaseModel):
        name: str
        as_vendor: bool = True
        prefix_names: bool = True

    @app.get("/api/settings/orca-group")
    def get_group():
        return orca_group()

    @app.put("/api/settings/orca-group")
    def set_group(body: GroupIn):
        name = body.name.strip()
        if len(name) > 40 or not re.fullmatch(r"[\w .+&-]*", name):
            raise HTTPException(400, "Use letters, numbers, spaces and . + & - (max 40)")
        db.set_setting("orca_group", {**body.model_dump(), "name": name})
        return orca_group()

    @app.get("/api/backup")
    def backup():
        """Download the whole database (all filaments, profiles, history)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("forge.sql", "\n".join(db.conn.iterdump()))
            if PHOTO_DIR.is_dir():
                for ph in PHOTO_DIR.iterdir():
                    z.write(ph, f"photos/{ph.name}")
        stamp = time.strftime("%Y%m%d-%H%M")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="filament-forge-backup-{stamp}.zip"'})

    @app.post("/api/backup/restore")
    async def restore(file: UploadFile = File(...)):
        """Replace everything with a backup zip made by /api/backup."""
        raw = await file.read()
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
            sql = z.read("forge.sql").decode("utf-8")
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
            raise HTTPException(400, "That isn't a Filament Forge backup zip")
        src = sqlite3.connect(":memory:")
        try:
            src.executescript(sql)
            if not src.execute("SELECT name FROM sqlite_master WHERE name='profiles'").fetchone():
                raise HTTPException(400, "Backup has no profiles table")
        except sqlite3.Error as e:
            raise HTTPException(400, f"Backup is damaged: {e}")
        # keep a safety copy of the current data first
        safety = DATA_DIR / f"before-restore-{int(time.time())}.sql"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        safety.write_text("\n".join(db.conn.iterdump()), encoding="utf-8")
        src.backup(db.conn)
        db.conn.executescript(SCHEMA_SQL)
        db._migrate()
        src.close()
        for name in z.namelist():
            if name.startswith("photos/") and Path(name).suffix.lower() in PHOTO_EXT:
                PHOTO_DIR.mkdir(parents=True, exist_ok=True)
                (PHOTO_DIR / Path(name).name).write_bytes(z.read(name))
        return {"ok": True, "safety_copy": str(safety)}

    # --------------------------------------------------------------------- AI
    class AISettings(BaseModel):
        enabled: bool | None = None
        mode: str | None = None
        gpu: str | None = None
        model: str | None = None
        external_url: str | None = None
        port: int | None = None
        context: int | None = None

    @app.get("/api/ai/status")
    def ai_status():
        s = ai.snapshot()
        s["disk_gb"] = ai.disk_usage_gb()
        return s

    @app.put("/api/ai/settings")
    def ai_settings(body: AISettings):
        changes = body.model_dump(exclude_none=True)
        if "mode" in changes and changes["mode"] not in ("bundled", "docker", "external"):
            raise HTTPException(400, "mode must be bundled, docker or external")
        if "gpu" in changes and changes["gpu"] not in ("auto", "nvidia", "amd", "cpu"):
            raise HTTPException(400, "gpu must be auto, nvidia, amd or cpu")
        if "context" in changes and changes["context"] not in CONTEXT_CHOICES:
            raise HTTPException(400, f"context must be one of {CONTEXT_CHOICES}")
        if "model" in changes and not re.fullmatch(r"[\w./:-]{1,100}", changes["model"]):
            raise HTTPException(400, "That doesn't look like an Ollama model name")
        restart = any(k in changes and changes[k] != ai.settings.get(k)
                      for k in ("mode", "port", "external_url", "gpu"))
        if restart:
            ai.stop()
        ai.settings.update(changes)
        db.set_setting("ai", ai.settings)
        if ai.settings["enabled"] and manage_ai:
            ai.start_async()  # also pulls a newly chosen model
        elif not ai.settings["enabled"]:
            ai.stop()
        return ai_status()

    @app.post("/api/ai/start")
    def ai_start():
        ai.settings["enabled"] = True
        db.set_setting("ai", ai.settings)
        ai.start_async()
        return ai_status()

    @app.post("/api/ai/stop")
    def ai_stop():
        ai.stop()
        return ai_status()

    @app.post("/api/ai/uninstall")
    def ai_uninstall():
        ai.uninstall()
        ai.settings["enabled"] = False
        db.set_setting("ai", ai.settings)
        return ai_status()

    @app.post("/api/ai/extract")
    def ai_extract_route(
        url: str | None = Form(None),
        text: str | None = Form(None),
        file: UploadFile | None = File(None),
    ):
        if not ai.ready():
            raise HTTPException(409, "The AI isn't ready yet. Set it up in the AI tab first.")
        data = name = None
        if file is not None and file.filename:
            data = file.file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(400, "That file is over 20 MB")
            name = file.filename
            if Path(name).suffix.lower() in PHOTO_EXT:
                data = shrink_image(data)
        try:
            return ai_extract.extract(ai.chat, url=(url or "").strip() or None, text=text,
                                      file_bytes=data, filename=name)
        except ai_extract.FetchError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"AI step failed: {e}")

    @app.exception_handler(HTTPException)
    async def http_err(_, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
    return app

"use strict";
/* Filament Forge front end: plain JS, no build step. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (t) => t ? new Date(t * 1000).toLocaleString() : "";
const SRC_LABEL = { calibrated: "Calibrated", manual: "You set", printer: "Printer limit",
  manufacturer: "Manufacturer", baseline: "Material default" };

const state = { meta: null, printers: [], filaments: [], profiles: [], current: null,
  calTest: null, calProfile: null };

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) {
    o.body = JSON.stringify(o.json);
    o.headers = { "Content-Type": "application/json" };
    o.method = o.method || "POST";
    delete o.json;
  }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    let msg = body?.error || body?.detail || body || r.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
    throw new Error(msg);
  }
  return body;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = ""), err ? 5000 : 2500);
}
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

/* ---------- dialog ---------- */
function openDialog(html, onSubmit) {
  const dlg = $("#dlg"), form = $("#dlg-form");
  form.innerHTML = html;
  form.onsubmit = async (ev) => {
    const btn = ev.submitter;
    if (!btn || btn.value === "cancel") return;
    ev.preventDefault();
    try { if ((await onSubmit(new FormData(form), form)) !== false) dlg.close(); }
    catch (e) { toast(e.message, true); }
  };
  dlg.showModal();
  return form;
}

/* ---------- navigation ---------- */
function show(view) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  ({ filaments: renderFilaments, profiles: loadProfiles, calibrate: renderCalibrate,
    printers: renderPrinters, backup: renderBackup })[view]?.();
  try { localStorage.setItem("ff-view", view); } catch {}
}
$$("#tabs button").forEach((b) => (b.onclick = () => show(b.dataset.view)));

/* ======================= FILAMENTS ======================= */
async function loadFilaments() { state.filaments = await api("/api/filaments"); }

async function renderFilaments() {
  await loadFilaments();
  const q = $("#fil-search").value.toLowerCase();
  const items = state.filaments.filter((f) =>
    !q || [f.brand, f.name, f.material, f.colour_name, f.store].join(" ").toLowerCase().includes(q));
  const el = $("#filament-list");
  if (!state.filaments.length) {
    el.innerHTML = `<div class="empty" style="grid-column:1/-1">No filaments yet. Add the first spool you bought.</div>`;
    return;
  }
  el.innerHTML = items.map((f) => {
    const spec = f.spec || {};
    const temp = spec.nozzle_min || spec.nozzle_max ? `${spec.nozzle_min ?? "?"}–${spec.nozzle_max ?? "?"} °C` : "";
    return `<div class="card">
      <div class="title"><span class="swatch" style="background:${esc(f.colour_hex || "#ccc")}"></span>
        ${esc([f.brand, f.name].filter(Boolean).join(" "))}</div>
      <div class="meta"><span class="tag">${esc(f.material)}</span> ${esc(f.colour_name || "")}
        ${f.profile_count ? `· ${f.profile_count} profile${f.profile_count > 1 ? "s" : ""}` : ""}</div>
      <dl class="kv">
        ${f.purchased_on ? `<dt>Bought</dt><dd>${esc(f.purchased_on)}${f.store ? " · " + esc(f.store) : ""}</dd>` : ""}
        ${f.price ? `<dt>Price</dt><dd>${esc(f.price)} ${esc(f.currency || "")}${f.weight_g ? ` / ${esc(f.weight_g)} g` : ""}</dd>` : ""}
        <dt>Spools</dt><dd>${esc(f.spools_left ?? f.spools ?? 1)} left of ${esc(f.spools ?? 1)}</dd>
        ${temp ? `<dt>Spec</dt><dd>${esc(temp)}</dd>` : ""}
        ${f.url ? `<dt>Link</dt><dd><a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">store page</a></dd>` : ""}
      </dl>
      ${f.notes ? `<p class="small muted">${esc(f.notes)}</p>` : ""}
      <div class="actions">
        <button class="primary" data-act="profile" data-id="${f.id}">Make profile</button>
        <button data-act="edit" data-id="${f.id}">Edit</button>
        <button class="danger" data-act="del" data-id="${f.id}">Delete</button>
      </div></div>`;
  }).join("") || `<p class="muted">Nothing matches.</p>`;
}

$("#fil-search").oninput = () => renderFilaments();
$("#add-filament").onclick = () => filamentForm();
$("#filament-list").onclick = guard(async (ev) => {
  const b = ev.target.closest("button[data-act]");
  if (!b) return;
  const f = state.filaments.find((x) => x.id == b.dataset.id);
  if (b.dataset.act === "edit") filamentForm(f);
  if (b.dataset.act === "profile") makeProfileDialog(f);
  if (b.dataset.act === "del" && confirm(`Delete ${f.name}? Its profiles are kept.`)) {
    await api(`/api/filaments/${f.id}`, { method: "DELETE" });
    renderFilaments();
  }
});

function filamentForm(f = {}) {
  const spec = f.spec || {};
  const mats = state.meta.materials.map((m) =>
    `<option ${m.name === (f.material || "PLA") ? "selected" : ""}>${esc(m.name)}</option>`).join("");
  const v = (x) => esc(x ?? "");
  openDialog(`
    <h2>${f.id ? "Edit filament" : "Add filament"}</h2>
    <div class="row">
      <div><label>Brand</label><input name="brand" value="${v(f.brand)}" placeholder="e.g. Polymaker"></div>
      <div><label>Name *</label><input name="name" required value="${v(f.name)}" placeholder="e.g. PolyTerra PLA"></div>
      <div><label>Material</label><select name="material">${mats}</select></div>
    </div>
    <div class="row">
      <div><label>Colour name</label><input name="colour_name" value="${v(f.colour_name)}"></div>
      <div><label>Colour</label><input type="color" name="colour_hex" value="${v(f.colour_hex || "#808080")}"></div>
      <div><label>Diameter (mm)</label><input type="number" step="0.01" name="diameter" value="${v(f.diameter ?? 1.75)}"></div>
      <div><label>Spool weight (g)</label><input type="number" name="weight_g" value="${v(f.weight_g ?? 1000)}"></div>
    </div>
    <div class="row">
      <div><label>Price</label><input type="number" step="0.01" name="price" value="${v(f.price)}"></div>
      <div><label>Currency</label><input name="currency" value="${v(f.currency)}" placeholder="USD"></div>
      <div><label>Bought on</label><input type="date" name="purchased_on" value="${v(f.purchased_on)}"></div>
      <div><label>Store</label><input name="store" value="${v(f.store)}"></div>
    </div>
    <div class="row">
      <div><label>Spools bought</label><input type="number" name="spools" min="0" value="${v(f.spools ?? 1)}"></div>
      <div><label>Spools left</label><input type="number" name="spools_left" min="0" value="${v(f.spools_left ?? f.spools ?? 1)}"></div>
    </div>
    <div><label>Store page link</label><input type="url" name="url" value="${v(f.url)}" placeholder="https://…"></div>
    <h3>Manufacturer specs (from the spool or store page)</h3>
    <p class="small muted">Anything you fill in here beats the generic material defaults. Leave blank if unknown.</p>
    <div class="row">
      <div><label>Nozzle min °C</label><input type="number" name="spec.nozzle_min" value="${v(spec.nozzle_min)}"></div>
      <div><label>Nozzle max °C</label><input type="number" name="spec.nozzle_max" value="${v(spec.nozzle_max)}"></div>
      <div><label>Bed min °C</label><input type="number" name="spec.bed_min" value="${v(spec.bed_min)}"></div>
      <div><label>Bed max °C</label><input type="number" name="spec.bed_max" value="${v(spec.bed_max)}"></div>
      <div><label>Density g/cm³</label><input type="number" step="0.01" name="spec.density" value="${v(spec.density)}"></div>
      <div><label>Max flow mm³/s</label><input type="number" step="0.5" name="spec.max_volumetric_speed" value="${v(spec.max_volumetric_speed)}"></div>
    </div>
    <div><label>Notes</label><textarea name="notes" rows="2">${v(f.notes)}</textarea></div>
    <div class="actions"><button class="primary" value="save">Save</button><button value="cancel" formnovalidate>Cancel</button></div>
  `, async (fd) => {
    const body = { spec: {} };
    for (const [k, val] of fd.entries()) {
      if (k.startsWith("spec.")) { if (val !== "") body.spec[k.slice(5)] = Number(val); }
      else body[k] = val === "" ? null : val;
    }
    for (const k of ["diameter", "weight_g", "price", "spools", "spools_left"])
      if (body[k] != null) body[k] = Number(body[k]);
    await api(f.id ? `/api/filaments/${f.id}` : "/api/filaments", { method: f.id ? "PUT" : "POST", json: body });
    toast("Saved");
    renderFilaments();
  });
}

function printerOptions(selected) {
  return `<option value="">No printer</option>` + state.printers.map((p) =>
    `<option value="${p.id}" ${p.id == selected ? "selected" : ""}>${esc(p.name)}</option>`).join("");
}

async function makeProfileDialog(f) {
  await loadPrinters();
  openDialog(`
    <h2>New profile for ${esc([f.brand, f.name].filter(Boolean).join(" "))}</h2>
    <p class="muted">Starts from ${esc(f.material)} defaults, applies the specs you entered, then caps anything your printer can't do.</p>
    <div class="row two">
      <div><label>Printer</label><select name="printer_id">${printerOptions(state.printers[0]?.id)}</select></div>
      <div><label>Profile name (optional)</label><input name="name" placeholder="auto"></div>
    </div>
    <div class="actions"><button class="primary" value="go">Create profile</button><button value="cancel" formnovalidate>Cancel</button></div>
  `, async (fd) => {
    const p = await api("/api/profiles", { json: {
      filament_id: f.id, printer_id: fd.get("printer_id") ? Number(fd.get("printer_id")) : null,
      name: fd.get("name") || null } });
    state.current = p.id;
    show("profiles");
  });
}

/* ======================= PROFILES ======================= */
async function loadProfiles() {
  state.profiles = await api("/api/profiles");
  const el = $("#profile-list");
  if (!state.profiles.length) {
    el.innerHTML = `<div class="empty">No profiles yet.</div>`;
    return;
  }
  el.innerHTML = state.profiles.map((p) => `
    <div class="item ${p.id === state.current ? "active" : ""}" data-id="${p.id}">
      <div class="name"><span class="swatch" style="background:${esc(p.colour_hex || "#ccc")}"></span>${esc(p.name)}</div>
      <div class="small muted">${esc(p.material || "")} · ${esc(p.printer_name || "no printer")} · ${p.confidence}% confident</div>
    </div>`).join("");
  if (state.current && state.profiles.some((p) => p.id === state.current)) renderProfile(state.current);
}
$("#profile-list").onclick = (ev) => {
  const it = ev.target.closest(".item");
  if (!it) return;
  state.current = Number(it.dataset.id);
  $$("#profile-list .item").forEach((x) => x.classList.toggle("active", x === it));
  renderProfile(state.current);
};

function gaugeColour(v) { return v >= 80 ? "var(--good)" : v >= 60 ? "var(--warn)" : "var(--bad)"; }

const renderProfile = guard(async (id) => {
  await loadPrinters();
  const p = await api(`/api/profiles/${id}`);
  const el = $("#profile-detail");
  const conf = p.confidence;
  const fields = state.meta.fields;
  const known = new Set(fields.map((f) => f.key));
  const groups = [...new Set(fields.map((f) => f.group))];
  const extra = Object.keys(p.settings).filter((k) => !known.has(k)).sort();

  const row = (key, label, unit, step) => {
    const val = p.settings[key];
    const src = p.sources[key];
    const c = conf.settings[key];
    const isText = typeof val === "string" && isNaN(Number(val));
    const range = c && c.low != null && src !== "calibrated"
      ? `<span class="range">likely ${c.low}–${c.high}</span>` : "";
    return `<tr><td>${esc(label)} ${unit ? `<span class="muted small">(${esc(unit)})</span>` : ""}</td>
      <td><input data-key="${esc(key)}" ${isText ? "" : `type="text" inputmode="decimal"`} value="${esc(val ?? "")}"
         data-orig="${esc(val ?? "")}" ${step ? `data-step="${step}"` : ""}></td>
      <td>${src ? `<span class="src ${esc(src)}">${esc(SRC_LABEL[src] || src)}</span>` : ""}</td>
      <td>${range}</td></tr>`;
  };

  const sp = p.speed;
  el.innerHTML = `
    <div class="head">
      <div class="gauge" style="--v:${conf.overall};--gc:${gaugeColour(conf.overall)}"><span>${conf.overall}%</span></div>
      <div style="flex:1;min-width:240px">
        <input id="pf-name" value="${esc(p.name)}" style="font-size:18px;font-weight:600">
        <div class="row two" style="margin-top:8px">
          <div><label>Printer</label><select id="pf-printer">${printerOptions(p.printer_id)}</select></div>
          <div><label>Orca parent preset</label><input id="pf-inherits" value="${esc(p.inherits || "")}"
            title="The Orca system preset this inherits from. Leave blank for a standalone preset."></div>
        </div>
      </div>
    </div>
    <div class="legend" style="margin:10px 0">Where each value came from:
      ${Object.entries(SRC_LABEL).map(([k, v]) => `<span class="src ${k}">${v}</span>`).join("")}</div>

    <div class="actions">
      <button class="primary" id="pf-save">Save changes</button>
      <a class="btn" href="/api/profiles/${p.id}/export">Export .json for Orca</a>
      <button id="pf-install">Install into Orca</button>
      <button id="pf-cal">Calibrate</button>
      <button id="pf-rebuild" title="Recalculate from material, specs and printer. Your edits and calibrations are kept.">Rebuild</button>
      <button id="pf-preview">Show JSON</button>
      <button id="pf-dup">Duplicate</button>
      <button class="danger" id="pf-del">Delete</button>
    </div>

    <h2>Speed check</h2>
    <div class="speed">
      <div><span class="muted small">Fastest at ${sp.layer_height} mm layers × ${sp.line_width} mm lines</span>
        <b>${sp.effective_max_speed ?? "?"} mm/s</b></div>
      <div><span class="muted small">Filament flow allows</span><b>${sp.flow_limited_speed ?? "?"} mm/s</b></div>
      <div><span class="muted small">Printer max</span><b>${sp.machine_max_speed ?? "?"} mm/s</b></div>
      <div><span class="muted small">Limited by</span><b>${esc(sp.limited_by || "?")}</b></div>
    </div>

    ${p.notes?.length ? `<div class="notes"><ul style="margin:0;padding-left:18px">${p.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}

    ${groups.map((g) => {
      const rows = fields.filter((f) => f.group === g).map((f) => row(f.key, f.label, f.unit, f.step)).join("");
      return `<h3>${esc(g)}</h3><table class="settings">${rows}</table>`;
    }).join("")}
    ${extra.length ? `<h3>Other settings</h3><table class="settings">${extra.map((k) => row(k, k, "", null)).join("")}</table>` : ""}

    <h2>Calibration log</h2>
    ${p.calibrations.length ? p.calibrations.map(calCard).join("") : `<p class="muted">No calibrations yet.</p>`}

    <h2>History</h2>
    <ul class="history">${p.versions.map((v, i) => `<li>${esc(fmtDate(v.created))} — ${esc(v.reason || "")}
      ${i > 0 ? `<button class="link" data-restore="${v.id}">restore</button>` : `<span class="tag">current</span>`}</li>`).join("")}</ul>
    ${p.exports.length ? `<h2>Exports</h2><ul class="history">${p.exports.map((x) =>
      `<li>${esc(fmtDate(x.created))} — ${esc(x.target)}</li>`).join("")}</ul>` : ""}
  `;

  $$("table.settings input", el).forEach((inp) =>
    (inp.oninput = () => inp.classList.toggle("changed", inp.value !== inp.dataset.orig)));

  $("#pf-save").onclick = guard(async () => {
    const settings = {};
    $$("table.settings input.changed", el).forEach((inp) => {
      const v = inp.value.trim();
      settings[inp.dataset.key] = v === "" ? null : (isNaN(Number(v)) ? v : Number(v));
    });
    await api(`/api/profiles/${p.id}`, { method: "PUT", json: {
      name: $("#pf-name").value, inherits: $("#pf-inherits").value,
      printer_id: Number($("#pf-printer").value) || 0, settings } });
    toast("Saved");
    loadProfiles();
  });
  $("#pf-rebuild").onclick = guard(async () => {
    await api(`/api/profiles/${p.id}`, { method: "PUT", json: { printer_id: Number($("#pf-printer").value) || 0 } });
    await api(`/api/profiles/${p.id}/rebuild`, { method: "POST" });
    toast("Rebuilt");
    loadProfiles();
  });
  $("#pf-dup").onclick = guard(async () => {
    const d = await api(`/api/profiles/${p.id}/duplicate`, { method: "POST" });
    state.current = d.id;
    loadProfiles();
  });
  $("#pf-del").onclick = guard(async () => {
    if (!confirm(`Delete profile "${p.name}"? (Exports already in Orca are not touched.)`)) return;
    await api(`/api/profiles/${p.id}`, { method: "DELETE" });
    state.current = null;
    $("#profile-detail").innerHTML = `<p class="muted">Deleted.</p>`;
    loadProfiles();
  });
  $("#pf-preview").onclick = guard(async () => {
    const j = await api(`/api/profiles/${p.id}/preview`);
    openDialog(`<h2>Orca preset preview</h2><pre class="json">${esc(JSON.stringify(j, null, 2))}</pre>
      <div class="actions"><button value="cancel">Close</button></div>`, () => {});
  });
  $("#pf-install").onclick = () => installDialog(p);
  $("#pf-cal").onclick = () => { state.calProfile = p.id; show("calibrate"); };
  $$("[data-restore]", el).forEach((b) => (b.onclick = guard(async () => {
    await api(`/api/profiles/${p.id}/restore/${b.dataset.restore}`, { method: "POST" });
    toast("Restored");
    loadProfiles();
  })));
  $$("[data-delcal]", el).forEach((b) => (b.onclick = guard(async () => {
    if (!confirm("Remove this log entry? (The profile values stay as they are.)")) return;
    await api(`/api/calibrations/${b.dataset.delcal}`, { method: "DELETE" });
    renderProfile(p.id);
  })));
});

function calCard(c) {
  const t = state.meta.tests.find((x) => x.id === c.test);
  return `<div class="card" style="margin-bottom:8px">
    <div class="title">${esc(t?.title || c.test)} <span class="muted small">${esc(fmtDate(c.created))}</span>
      ${c.applied ? `<span class="src calibrated">applied</span>` : `<span class="tag">logged only</span>`}</div>
    ${c.setting ? `<div class="small">${esc(c.setting)}: ${esc(c.old_value ?? "—")} → <b>${esc(c.new_value ?? "—")}</b></div>` : ""}
    ${c.notes ? `<div class="small muted">${esc(c.notes)}</div>` : ""}
    ${c.photos?.length ? `<div class="photos">${c.photos.map((ph) =>
      `<a href="/photos/${encodeURIComponent(ph)}" target="_blank"><img src="/photos/${encodeURIComponent(ph)}" alt="calibration photo"></a>`).join("")}</div>` : ""}
    <button class="link small" data-delcal="${c.id}">remove entry</button>
  </div>`;
}

function installDialog(p) {
  const dirs = state.meta.orca_filament_dirs;
  if (!dirs.length) {
    openDialog(`<h2>Install into Orca</h2>
      <p>Couldn't find OrcaSlicer's user preset folder${state.meta.orca_config_dir ? ` under <code>${esc(state.meta.orca_config_dir)}</code>` : ""}.</p>
      <p>Use <b>Export .json for Orca</b> instead, then in Orca: <b>File → Import → Import Configs…</b> and pick the file.</p>
      <p class="small muted">If Orca lives somewhere else, start this app with the ORCA_CONFIG_DIR environment variable set to that folder.</p>
      <div class="actions"><button value="cancel">Close</button></div>`, () => {});
    return;
  }
  openDialog(`<h2>Install into Orca</h2>
    <p>Writes <b>${esc(p.name)}</b> into Orca's user filament folder. If a preset with this name already exists it is replaced
    (the old one is in your Orca snapshot if you took one).</p>
    <label>Orca account folder</label>
    <select name="dir">${dirs.map((d) => `<option>${esc(d)}</option>`).join("")}</select>
    <p class="small muted">"default" is used when you're not logged in to Orca. Close Orca first, or restart it afterwards.</p>
    <div class="actions"><button class="primary" value="go">Install</button><button value="cancel" formnovalidate>Cancel</button></div>`,
  async (fd) => {
    const r = await api(`/api/profiles/${p.id}/install`, { json: { target_dir: fd.get("dir") } });
    toast(r.message);
    renderProfile(p.id);
  });
}

/* ======================= CALIBRATE ======================= */
async function renderCalibrate() {
  state.profiles = await api("/api/profiles");
  const sel = $("#cal-profile");
  if (!state.profiles.length) {
    sel.innerHTML = "";
    $("#cal-body").innerHTML = `<div class="empty">Make a profile first, then come back to calibrate it.</div>`;
    return;
  }
  const pid = state.calProfile && state.profiles.some((p) => p.id === state.calProfile)
    ? state.calProfile : (state.current || state.profiles[0].id);
  state.calProfile = pid;
  sel.innerHTML = state.profiles.map((p) =>
    `<option value="${p.id}" ${p.id === pid ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  await renderCalTest();
}
$("#cal-profile").onchange = () => { state.calProfile = Number($("#cal-profile").value); renderCalTest(); };

const renderCalTest = guard(async () => {
  const p = await api(`/api/profiles/${state.calProfile}`);
  const tests = state.meta.tests;
  const done = new Set(p.calibrations.filter((c) => c.applied || c.test === "vfa").map((c) => c.test));
  if (!state.calTest) state.calTest = (tests.find((t) => !done.has(t.id) && t.id !== "flow_yolo") || tests[0]).id;
  const t = tests.find((x) => x.id === state.calTest);
  const cur = t.setting ? p.settings[t.setting] : null;

  $("#cal-body").innerHTML = `
    <p class="muted">Suggested order: temperature → flow → pressure advance → retraction → max flow. Export to Orca between steps so each
    test prints with the latest values.</p>
    <div class="steps">${tests.map((x, i) => `<div class="step ${x.id === t.id ? "active" : ""} ${done.has(x.id) ? "done" : ""}" data-test="${x.id}">
      <div class="n">${x.id === "flow_yolo" ? "Or instead of 2 + 3" : "Step " + x.order}</div><div>${esc(x.title)}</div></div>`).join("")}</div>
    <div class="detail">
      <h2 style="margin-top:0">${esc(t.title)}</h2>
      <p>In OrcaSlicer: <b>${esc(t.orca_menu)}</b>, with this filament profile selected.</p>
      <ol>${t.howto.map((h) => `<li>${esc(h)}</li>`).join("")}</ol>
      ${t.setting ? `<p class="small muted">Current ${esc(t.setting)}: <b>${esc(cur ?? "not set")}</b>
        ${p.sources[t.setting] ? `<span class="src ${esc(p.sources[t.setting])}">${esc(SRC_LABEL[p.sources[t.setting]])}</span>` : ""}</p>` : ""}
      <form id="cal-form">
        <div class="row">${t.inputs.map((i) => `<div><label>${esc(i.label)}</label>
          <input type="number" step="${i.step ?? "any"}" name="${esc(i.key)}" value="${esc(i.default ?? "")}"></div>`).join("")}</div>
        <div id="cal-result" class="result muted">Enter your reading to see the new value.</div>
        <label>Photos of the print (optional, for your records)</label>
        <div class="drop"><input type="file" name="photos" accept="image/*" multiple></div>
        <div id="cal-thumbs" class="photos"></div>
        <label style="margin-top:10px">Notes</label>
        <textarea name="notes" rows="2" placeholder="What you saw, which toolhead, anything odd…"></textarea>
        <label style="margin-top:10px;display:flex;gap:8px;align-items:center;color:var(--text)">
          <input type="checkbox" name="unsure"> Not sure: log it with the photos but don't change the profile yet</label>
        <div class="actions"><button class="primary" type="submit">Save result</button></div>
      </form>
    </div>
    <h2>Past results for this profile</h2>
    ${p.calibrations.length ? p.calibrations.map(calCard).join("") : `<p class="muted">None yet.</p>`}
  `;

  $$(".step").forEach((s) => (s.onclick = () => { state.calTest = s.dataset.test; renderCalTest(); }));
  const form = $("#cal-form");
  const readInputs = () => Object.fromEntries(t.inputs.map((i) => [i.key, form.elements[i.key].value]));
  const preview = async () => {
    const box = $("#cal-result");
    const inputs = readInputs();
    if (t.inputs.some((i) => i.default === undefined && inputs[i.key] === "")) {
      box.className = "result muted"; box.textContent = "Enter your reading to see the new value."; return;
    }
    try {
      const r = await api(`/api/profiles/${p.id}/calibrations/preview`, { json: { test: t.id, inputs } });
      box.className = "result";
      box.innerHTML = r.setting
        ? `New ${esc(r.setting)}: <b>${esc(r.new)}</b> <span class="muted">(was ${esc(r.old ?? "not set")})</span><br><span class="small muted">${esc(r.explanation)}</span>`
        : esc(r.explanation);
    } catch (e) { box.className = "result"; box.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  };
  form.oninput = (ev) => { if (ev.target.type === "number") preview(); };
  form.elements.photos.onchange = () => {
    $("#cal-thumbs").innerHTML = [...form.elements.photos.files].map((f) =>
      `<img src="${URL.createObjectURL(f)}" alt="">`).join("");
  };
  form.onsubmit = guard(async (ev) => {
    ev.preventDefault();
    const fd = new FormData();
    fd.append("test", t.id);
    fd.append("inputs", JSON.stringify(readInputs()));
    fd.append("notes", form.elements.notes.value);
    fd.append("apply", form.elements.unsure.checked ? "false" : "true");
    for (const f of form.elements.photos.files) fd.append("photos", f);
    await api(`/api/profiles/${p.id}/calibrations`, { method: "POST", body: fd });
    toast(form.elements.unsure.checked ? "Logged (profile unchanged)" : "Saved and applied. Export to Orca before the next test.");
    const next = tests.find((x) => x.order > t.order && x.id !== "flow_yolo");
    state.calTest = next ? next.id : t.id;
    renderCalTest();
  });
  if (t.inputs.every((i) => i.default !== undefined)) preview();
});

/* ======================= PRINTERS ======================= */
const PRINTER_FIELDS = [
  ["orca_printer", "Orca printer preset name", "text"],
  ["kinematics", "Kinematics", "text"],
  ["toolheads", "Toolheads", "number"],
  ["nozzle_diameter", "Nozzle (mm)", "number"],
  ["max_velocity", "Max speed (mm/s)", "number"],
  ["max_accel", "Max accel (mm/s²)", "number"],
  ["square_corner_velocity", "Square corner velocity", "number"],
  ["nozzle_max_temp", "Hotend max temp (°C)", "number"],
  ["bed_max_temp", "Bed max temp (°C)", "number"],
  ["hotend_max_flow", "Hotend max flow (mm³/s)", "number"],
  ["max_extrude_cross_section", "Max extrude cross-section", "number"],
  ["bed_x", "Bed X (mm)", "number"], ["bed_y", "Bed Y (mm)", "number"], ["bed_z", "Height (mm)", "number"],
];

async function loadPrinters() { state.printers = await api("/api/printers"); }

async function renderPrinters() {
  await loadPrinters();
  $("#printer-list").innerHTML = state.printers.map((p) => `
    <div class="card">
      <div class="title">${esc(p.name)}</div>
      <dl class="kv" style="margin-top:8px">${PRINTER_FIELDS.map(([k, l]) =>
        `<dt>${esc(l)}</dt><dd>${p.data[k] != null && p.data[k] !== "" ? esc(p.data[k]) : `<span class="muted">not set</span>`}</dd>`).join("")}
        <dt>Only show in Orca for this printer</dt><dd>${p.data.restrict_to_printer ? "yes" : "no"}</dd></dl>
      ${p.data.notes ? `<p class="small muted">${esc(p.data.notes)}</p>` : ""}
      <div class="actions">
        <button class="primary" data-act="cfg" data-id="${p.id}">Import printer.cfg</button>
        <button data-act="edit" data-id="${p.id}">Edit</button>
        <button class="danger" data-act="del" data-id="${p.id}">Delete</button>
      </div></div>`).join("") || `<div class="empty">No printers.</div>`;
}
$("#add-printer").onclick = () => printerForm({ name: "", data: {} });
$("#printer-list").onclick = guard(async (ev) => {
  const b = ev.target.closest("button[data-act]");
  if (!b) return;
  const p = state.printers.find((x) => x.id == b.dataset.id);
  if (b.dataset.act === "edit") printerForm(p);
  if (b.dataset.act === "cfg") klipperDialog(p);
  if (b.dataset.act === "del" && confirm(`Delete ${p.name}?`)) {
    await api(`/api/printers/${p.id}`, { method: "DELETE" });
    renderPrinters();
  }
});

function printerForm(p) {
  openDialog(`<h2>${p.id ? "Edit printer" : "Add printer"}</h2>
    <div><label>Name</label><input name="name" required value="${esc(p.name)}"></div>
    <div class="row" style="margin-top:10px">${PRINTER_FIELDS.map(([k, l, t]) => `<div><label>${esc(l)}</label>
      <input name="${k}" type="${t}" step="any" value="${esc(p.data[k] ?? "")}"></div>`).join("")}</div>
    <label style="display:flex;gap:8px;align-items:center;color:var(--text)"><input type="checkbox" name="restrict_to_printer"
      ${p.data.restrict_to_printer ? "checked" : ""}> Exported presets only show up in Orca for this printer preset</label>
    <div style="margin-top:10px"><label>Notes</label><textarea name="notes" rows="2">${esc(p.data.notes ?? "")}</textarea></div>
    <div class="actions"><button class="primary" value="save">Save</button><button value="cancel" formnovalidate>Cancel</button></div>`,
  async (fd) => {
    const data = { ...p.data };
    for (const [k, , t] of PRINTER_FIELDS) {
      const v = fd.get(k);
      data[k] = v === "" ? null : t === "number" ? Number(v) : v;
    }
    data.restrict_to_printer = fd.get("restrict_to_printer") === "on";
    data.notes = fd.get("notes");
    await api(p.id ? `/api/printers/${p.id}` : "/api/printers", { method: p.id ? "PUT" : "POST",
      json: { name: fd.get("name"), data } });
    toast("Saved");
    renderPrinters();
  });
}

function klipperDialog(p) {
  let parsed = null;
  const form = openDialog(`<h2>Import Klipper config into ${esc(p.name)}</h2>
    <p class="muted">Pick <code>printer.cfg</code> plus any files it includes (hold Ctrl/Cmd to pick several). On the U1 you can download them
    from the printer's web interface (Fluidd/Mainsail → Machine/Config).</p>
    <div class="drop"><input type="file" name="files" multiple accept=".cfg,.conf,.txt"></div>
    <div id="cfg-preview" style="margin-top:10px"></div>
    <div class="actions"><button class="primary" value="apply" id="cfg-apply" disabled>Apply to printer</button>
      <button value="cancel" formnovalidate>Cancel</button></div>`,
  async () => {
    if (!parsed) return false;
    await api(`/api/printers/${p.id}`, { method: "PUT", json: { name: p.name, data: { ...p.data, ...parsed.fields } } });
    toast("Printer updated. Use Rebuild on profiles to apply the new limits.");
    renderPrinters();
  });
  form.elements.files.onchange = guard(async () => {
    const fd = new FormData();
    for (const f of form.elements.files.files) fd.append("files", f);
    parsed = await api("/api/klipper/parse", { method: "POST", body: fd });
    const s = parsed.summary;
    $("#cfg-preview").innerHTML = `
      <dl class="kv">${Object.entries(parsed.fields).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("") ||
        "<dt>Nothing found</dt><dd>No [printer] or [extruder] sections in those files.</dd>"}</dl>
      ${s.extruders.length ? `<p class="small muted">Toolheads: ${s.extruders.map((e) =>
        `${esc(e.name)} (max ${esc(e.max_temp ?? "?")} °C${e.pressure_advance != null ? `, PA ${esc(e.pressure_advance)}` : ""})`).join(", ")}</p>` : ""}`;
    $("#cfg-apply").disabled = !Object.keys(parsed.fields).length;
  });
}

/* ======================= ORCA & BACKUP ======================= */
async function renderBackup() {
  const m = state.meta;
  const scan = await api("/api/orca/scan");
  await loadPrinters();
  $("#backup-body").innerHTML = `
    <div class="cards wide">
      <div class="card">
        <div class="title">OrcaSlicer folder</div>
        <p class="small">${m.orca_config_exists ? `Found at <code>${esc(m.orca_config_dir)}</code>`
          : `Not found${m.orca_config_dir ? ` at <code>${esc(m.orca_config_dir)}</code>` : ""}. Export files and use File → Import → Import Configs in Orca.`}</p>
        <p class="small muted">Orca updates normally keep your presets, but a preset can break if the system preset it inherits from gets renamed.
        Snapshots keep a copy of every user filament preset so you can always get them back.</p>
        <div class="actions"><button class="primary" id="snap">Snapshot Orca presets now</button></div>
      </div>
      <div class="card">
        <div class="title">This app's data</div>
        <p class="small muted">Everything (filament log, profiles, history, calibration photos) in one zip.</p>
        <div class="actions">
          <a class="btn primary" href="/api/backup">Download backup</a>
          <a class="btn" href="/api/export-all">All profiles as Orca .json (zip)</a>
          <button id="restore">Restore backup…</button>
        </div>
        <p class="small muted" style="margin-top:10px">Import a single Orca preset file:</p>
        <input type="file" id="imp-file" accept=".json">
      </div>
    </div>
    <h2>Filament presets in Orca (${scan.presets.length})</h2>
    ${scan.presets.length ? `<table class="settings">${scan.presets.map((x) => `<tr>
      <td><b>${esc(x.name)}</b><div class="small muted">${esc(x.inherits ? "inherits " + x.inherits : "standalone")} · account ${esc(x.account)}</div></td>
      <td class="small muted">${esc(fmtDate(x.modified))}</td>
      <td><button data-imp="${esc(x.path)}">Import as profile</button></td></tr>`).join("")}</table>`
      : `<p class="muted">None found.</p>`}
    <h2>Snapshots</h2>
    ${scan.snapshots.length ? `<ul class="history">${scan.snapshots.map((s) => `<li>${esc(fmtDate(s.created))} — ${esc(s.name)}
      <a href="/api/orca/snapshots/${s.id}">download</a></li>`).join("")}</ul>` : `<p class="muted">No snapshots yet.</p>`}
  `;
  $("#snap").onclick = guard(async () => {
    const r = await api("/api/orca/snapshot", { method: "POST" });
    toast(r.saved ? `Saved ${r.saved} changed preset(s)` : "Nothing changed since the last snapshot");
    renderBackup();
  });
  $$("[data-imp]").forEach((b) => (b.onclick = guard(async () => {
    const p = await api("/api/orca/import-path", { json: { path: b.dataset.imp } });
    toast(`Imported ${p.name}`);
  })));
  $("#imp-file").onchange = guard(async (ev) => {
    const fd = new FormData();
    fd.append("file", ev.target.files[0]);
    const p = await api("/api/orca/import-file", { method: "POST", body: fd });
    toast(`Imported ${p.name}`);
    state.current = p.id;
  });
  $("#restore").onclick = () => {
    const form = openDialog(`<h2>Restore backup</h2>
      <p>This replaces <b>everything</b> in the app with the backup. A safety copy of the current data is saved first.</p>
      <input type="file" name="file" accept=".zip" required>
      <div class="actions"><button class="primary" value="go">Restore</button><button value="cancel" formnovalidate>Cancel</button></div>`,
    async (fd) => {
      const r = await api("/api/backup/restore", { method: "POST", body: fd });
      toast("Restored. Safety copy: " + r.safety_copy);
      renderBackup();
    });
  };
}

/* ---------- boot ---------- */
(async () => {
  try {
    state.meta = await api("/api/meta");
    await loadPrinters();
    let view = "filaments";
    try { view = localStorage.getItem("ff-view") || view; } catch {}
    show(view);
  } catch (e) { toast("Couldn't reach the app server: " + e.message, true); }
})();

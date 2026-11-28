"use strict";
/* Filament Forge front end: plain JS, no build step. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (t) => t ? new Date(t * 1000).toLocaleString() : "";
const SRC_LABEL = { calibrated: "Calibrated", tested: "Proven in a print", manual: "You set", imported: "Imported", printer: "Printer limit",
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
    try { if ((await onSubmit(new FormData(form), form, btn.value)) !== false) dlg.close(); }
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
    printers: renderPrinters, backup: renderBackup, ai: renderAI })[view]?.();
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
  const form = openDialog(`
    <h2>${f.id ? "Edit filament" : "Add filament"}</h2>
    <details class="aibox" ${f.id ? "" : "open"}>
      <summary>Fill in with AI from a link, PDF, photo or text</summary>
      <p class="small muted" style="margin:6px 0 8px">Best sources: the maker's product page, the technical data sheet (PDF), or a photo of
        the spool label. Values found on the page get a green outline; amber means check it yourself.</p>
      <div class="row two">
        <div><label>Store or product page link</label><input type="url" id="ai-url" placeholder="https://…"></div>
        <div><label>…or a PDF / photo / saved page</label><input type="file" id="ai-file" accept=".pdf,image/*,.html,.htm,.txt"></div>
      </div>
      <div><label>…or paste the product text</label><textarea id="ai-text" rows="2"></textarea></div>
      <div class="actions" style="margin-top:8px"><button type="button" class="primary" id="ai-go">Read it</button>
        <span id="ai-msg" class="small muted" style="align-self:center"></span></div>
      <div id="ai-found"></div>
    </details>
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
  wireAIFill(form);
}

const FIELD_LABEL = { brand: "Brand", product_name: "Name", material: "Material", colour_name: "Colour",
  diameter: "Diameter", nozzle_min: "Nozzle min", nozzle_max: "Nozzle max", bed_min: "Bed min", bed_max: "Bed max",
  density: "Density", max_volumetric_speed: "Max flow", max_print_speed: "Max print speed", weight_g: "Weight",
  drying: "Drying", price: "Price", currency: "Currency" };
const FORM_TARGET = { brand: "brand", product_name: "name", colour_name: "colour_name", diameter: "diameter",
  weight_g: "weight_g", price: "price", currency: "currency", nozzle_min: "spec.nozzle_min",
  nozzle_max: "spec.nozzle_max", bed_min: "spec.bed_min", bed_max: "spec.bed_max", density: "spec.density",
  max_volumetric_speed: "spec.max_volumetric_speed" };

function wireAIFill(form) {
  const go = $("#ai-go", form);
  if (!go) return;
  go.onclick = async () => {
    const msg = $("#ai-msg", form), found = $("#ai-found", form);
    const st = await api("/api/ai/status").catch(() => null);
    if (!st || st.state !== "ready") {
      msg.innerHTML = `AI isn't running. <a href="#" id="ai-goto">Set it up in the AI tab</a>.`;
      $("#ai-goto", form).onclick = (e) => { e.preventDefault(); $("#dlg").close(); show("ai"); };
      return;
    }
    const fd = new FormData();
    const url = $("#ai-url", form).value.trim(), file = $("#ai-file", form).files[0], text = $("#ai-text", form).value;
    if (url) fd.append("url", url); else if (file) fd.append("file", file); else if (text.trim()) fd.append("text", text);
    else { msg.textContent = "Add a link, a file or some text first."; return; }
    go.disabled = true;
    msg.textContent = "Reading… (can take a minute without a graphics card)";
    found.innerHTML = "";
    try {
      const r = await api("/api/ai/extract", { method: "POST", body: fd });
      const set = (name, value, status) => {
        const el = form.elements[name];
        if (!el || value == null) return;
        el.value = value;
        el.classList.remove("ai-verified", "ai-unverified");
        el.classList.add(status === "verified" ? "ai-verified" : "ai-unverified");
      };
      for (const [k, v] of Object.entries(r.fields)) {
        if (v.status !== "rejected" && FORM_TARGET[k]) set(FORM_TARGET[k], v.value, v.status);
      }
      if (r.form.material) set("material", r.form.material, r.fields.material?.status || "unverified");
      if (r.form.notes) {
        const n = form.elements.notes;
        if (!n.value.includes(r.form.notes)) n.value = (n.value ? n.value + "\n" : "") + r.form.notes;
      }
      const rows = Object.entries(r.fields);
      msg.textContent = rows.length ? `Found ${rows.filter(([, v]) => v.status !== "rejected").length} values. Check them, then Save.`
        : "Nothing useful found. Try another source.";
      found.innerHTML = rows.length ? `<ul class="found">${rows.map(([k, v]) => `<li>
        <span class="st ${v.status}">${v.status === "verified" ? "found on page" : v.status === "unverified" ? "check" : "ignored"}</span>
        <b>${esc(FIELD_LABEL[k] || k)}:</b> ${esc(v.value)}
        ${v.quote ? `<span class="muted">— “${esc(v.quote)}”</span>` : ""}
        ${v.reason ? `<span class="muted">(${esc(v.reason)})</span>` : ""}</li>`).join("")}</ul>` : "";
    } catch (e) {
      msg.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`;
    } finally { go.disabled = false; }
  };
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

/* ---------- import Orca presets (.json) ---------- */
async function importPresets(fileList) {
  const files = [...fileList].filter((f) => /\.(json|zip)$/i.test(f.name));
  if (!files.length) { toast("Pick OrcaSlicer filament .json files (or a .zip of them)", true); return; }
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  const r = await api("/api/orca/import-file", { method: "POST", body: fd });
  const ok = r.results.filter((x) => !x.error), bad = r.results.filter((x) => x.error);
  const what = (x) => x.new_profile ? (x.new_filament ? "new filament + profile" : "new profile") :
    (x.changed.length ? `updated ${x.changed.length} setting${x.changed.length > 1 ? "s" : ""}` : "already up to date");
  if (ok.length === 1 && !bad.length) {
    toast(`Imported ${ok[0].name}: ${what(ok[0])}`);
    state.current = ok[0].profile_id;
    show("profiles");
    return;
  }
  openDialog(`<h2>Import results</h2>
    <ul class="found">${r.results.map((x) => `<li>${x.error
      ? `<span class="st rejected">failed</span> <b>${esc(x.file)}</b>: ${esc(x.error)}`
      : `<span class="st verified">ok</span> <b>${esc(x.name)}</b>: ${esc(what(x))}`}</li>`).join("")}</ul>
    <div class="actions"><button value="cancel" class="primary">OK</button></div>`, () => {});
  show("filaments");
}

$("#import-json").onclick = () => $("#import-json-file").click();
$("#import-link").onclick = () => openDialog(`<h2>Import from a QR swatch</h2>
  <p class="small muted">Scan the swatch with your phone's camera, copy the link it shows (long-press → Copy), send it to this
    computer (e.g. email it to yourself) and paste it here. This works even if the scan page isn't online.</p>
  <textarea name="link" rows="4" required placeholder="https://vlottiz.github.io/VlottizFillimentHub/#1*..."></textarea>
  <div class="actions"><button class="primary" value="go">Import</button><button value="cancel" formnovalidate>Cancel</button></div>`,
  async (fd) => {
    const p = await api("/api/import-swatch-link", { json: { link: fd.get("link") } });
    toast(`Imported ${p.name}: ${p.import.new_profile ? (p.import.new_filament ? "new filament + profile" : "new profile") : "updated"}`);
    state.current = p.id; show("profiles");
  });
$("#import-json-file").onchange = guard(async (ev) => { await importPresets(ev.target.files); ev.target.value = ""; });

// Drag .json files anywhere onto the app to import them.
let dragDepth = 0;
const hasFiles = (ev) => [...(ev.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (ev) => {
  if (!hasFiles(ev) || $("#dlg").open) return;
  dragDepth++; $("#drop-overlay").classList.add("show");
});
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").classList.remove("show"); } });
window.addEventListener("dragover", (ev) => { if (hasFiles(ev)) ev.preventDefault(); });
window.addEventListener("drop", guard(async (ev) => {
  if (!hasFiles(ev)) return;
  ev.preventDefault();
  dragDepth = 0; $("#drop-overlay").classList.remove("show");
  if ($("#dlg").open) return;  // dropping onto a dialog's own file box is handled there
  await importPresets(ev.dataTransfer.files);
}));

/* ======================= PROFILES ======================= */
async function loadProfiles() {
  api("/api/settings/orca-group").then((g) => {
    $("#bundle-btn").textContent = `Download ${g.name || "Filament Forge"} bundle for Orca`;
  }).catch(() => {});
  state.profiles = await api("/api/profiles");
  const el = $("#profile-list");
  if (!state.profiles.length) {
    el.innerHTML = `<div class="empty">No profiles yet.</div>`;
    return;
  }
  el.innerHTML = state.profiles.map((p) => `
    <div class="item ${p.id === state.current ? "active" : ""}" data-id="${p.id}">
      <div class="name"><span class="swatch" style="background:${esc(p.colour_hex || "#ccc")}"></span>${esc(p.name)}</div>
      <div class="small muted">${p.trial_of ? `<span class="tag trial">trial</span> ` : ""}${esc(p.material || "")} · ${esc(p.printer_name || "no printer")} · ${p.confidence}% confident</div>
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

const STATUS_LABEL = { diagnosed: "suggestions only", trial: "trial being tested", fixed: "fixed ✓",
  not_better: "trial didn't help", kept: "kept as its own profile" };

function changeRows(changes) {
  const label = (k) => state.meta.fields.find((f) => f.key === k)?.label || k;
  return `<table class="settings changes">${changes.filter((c) => !c.companion).map((c) => `<tr>
    <td><b>${esc(label(c.key))}</b><div class="small muted">${esc(c.why)}</div></td>
    <td class="nowrap">${esc(c.old ?? "printer's")} → <b>${esc(c.new)}</b></td></tr>`).join("")}</table>`;
}

function problemCard(t) {
  const issues = (t.diagnosis?.issues || []).map((i) => i.title || i.id).join(", ") || "Nothing found";
  return `<div class="card" style="margin-bottom:8px">
    <div class="title">${esc(issues)} <span class="muted small">${esc(fmtDate(t.created))}</span>
      <span class="tag">${esc(STATUS_LABEL[t.status] || t.status)}</span></div>
    ${t.description ? `<div class="small muted">“${esc(t.description)}”</div>` : ""}
    ${t.changes?.length ? `<div class="small">${t.changes.filter((c) => !c.companion).map((c) =>
      `${esc(c.key)}: ${esc(c.old ?? "printer's")} → <b>${esc(c.new)}</b>`).join(" · ")}</div>` : ""}
    ${t.photos?.length ? `<div class="photos">${t.photos.map((ph) =>
      `<a href="/photos/${encodeURIComponent(ph)}" target="_blank"><img src="/photos/${encodeURIComponent(ph)}" alt="problem photo"></a>`).join("")}</div>` : ""}
    ${t.status === "trial" && t.trial_profile_id ? `<button class="link small" data-open-profile="${t.trial_profile_id}">Open the trial profile</button>` : ""}
  </div>`;
}

function trialBanner(p) {
  if (!p.trial) return "";
  const s = p.trial.session, orig = p.trial.original;
  return `<div class="notes trial-banner">
    <b>Trial profile</b>: a copy of <button class="link" data-open-profile="${orig?.id}">${esc(orig?.name || "the original")}</button>
    with these changes${s?.diagnosis?.issues?.length ? ` to fix <b>${esc(s.diagnosis.issues.map((i) => i.title || i.id).join(", "))}</b>` : ""}:
    ${s?.changes?.length ? changeRows(s.changes) : ""}
    <ol class="small"><li>Export or install it (or download the bundle) and print the same model again.</li>
      <li>Compare the two prints, then:</li></ol>
    <div class="actions">
      <button class="primary" data-trial="better">✓ It's better: copy into the original</button>
      <button data-trial="not_better">✗ Not better: delete the trial</button>
      <button data-trial="keep">Keep as its own profile</button></div>
  </div>`;
}

async function fixDialog(p) {
  const [issues, st] = await Promise.all([api("/api/issues"), api("/api/ai/status").catch(() => null)]);
  const aiReady = st?.state === "ready";
  const form = openDialog(`<h2>Fix a print problem</h2>
    <p class="small muted">Add a photo of the problem and a sentence or two about it. ${aiReady
      ? "The AI looks at it and picks the likely problem; the app then suggests small, safe setting changes."
      : "The AI isn't running, so pick the problem yourself (or start the AI in the AI tab)."}</p>
    <label>Photos of the problem</label>
    <div class="drop"><input type="file" name="photos" accept="image/*" multiple></div>
    <div id="fix-thumbs" class="photos"></div>
    <label style="margin-top:10px">What's wrong?</label>
    <textarea name="description" rows="2" placeholder="e.g. Lots of thin strings between the two towers, and the top looks a bit rough."></textarea>
    <div class="row two" style="margin-top:10px">
      <label class="opt"><input type="radio" name="how" value="ai" ${aiReady ? "checked" : "disabled"}> Let the AI look at it</label>
      <label class="opt"><input type="radio" name="how" value="me" ${aiReady ? "" : "checked"}> I'll pick the problem</label>
    </div>
    <div id="fix-pick" class="issue-pick" ${aiReady ? "hidden" : ""}>${issues.map((i) => `<label class="opt">
      <input type="checkbox" name="issue" value="${esc(i.id)}"> <span><b>${esc(i.title)}</b>
      <span class="small muted">${esc(i.looks_like)}</span></span></label>`).join("")}</div>
    <label class="opt" style="margin-top:10px"><input type="checkbox" name="auto" checked>
      Auto-tweak: make a trial profile with the changes (your original isn't touched)</label>
    <div class="actions"><button class="primary" type="button" id="fix-go">Find the problem</button>
      <button value="cancel" formnovalidate>Close</button></div>
    <div id="fix-result"></div>`, () => {});

  const sync = () => { $("#fix-pick", form).hidden = form.elements.how.value !== "me"; };
  [...form.querySelectorAll("input[name=how]")].forEach((r) => (r.onchange = sync));
  form.elements.photos.onchange = () => {
    $("#fix-thumbs", form).innerHTML = [...form.elements.photos.files].map((f) => `<img src="${URL.createObjectURL(f)}" alt="">`).join("");
  };
  $("#fix-go", form).onclick = async () => {
    const box = $("#fix-result", form), btn = $("#fix-go", form);
    const fd = new FormData();
    fd.append("description", form.elements.description.value);
    for (const f of form.elements.photos.files) fd.append("photos", f);
    const me = form.elements.how.value === "me";
    if (me) {
      const picked = [...form.querySelectorAll("input[name=issue]:checked")].map((c) => c.value);
      if (!picked.length) { box.innerHTML = `<p class="small" style="color:var(--bad)">Tick at least one problem.</p>`; return; }
      fd.append("issues", picked.join(","));
      fd.append("use_ai", "false");
    } else if (!form.elements.photos.files.length && !form.elements.description.value.trim()) {
      box.innerHTML = `<p class="small" style="color:var(--bad)">Add a photo or describe the problem.</p>`; return;
    }
    btn.disabled = true;
    box.innerHTML = `<p class="small muted">${me ? "Working it out…" : "The AI is looking at it… (up to a minute without a graphics card)"}</p>`;
    try {
      const s = await api(`/api/profiles/${p.id}/diagnose`, { method: "POST", body: fd });
      const d = s.diagnosis;
      let trial = null;
      if (form.elements.auto.checked && s.changes.length) trial = await api(`/api/troubleshoots/${s.id}/trial`, { method: "POST" });
      box.innerHTML = `<h3>What it found</h3>
        ${d.issues.length ? `<ul class="found">${d.issues.map((i) => `<li><b>${esc(i.title)}</b>
          ${i.confidence != null ? `<span class="tag">${Math.round(i.confidence * 100)}% sure</span>` : ""}
          ${i.evidence && d.by === "ai" ? `<div class="small muted">${esc(i.evidence)}</div>` : ""}</li>`).join("")}</ul>`
          : `<p>No clear problem found. ${esc(d.summary || "")} Try a closer, well-lit photo, or pick the problem yourself.</p>`}
        ${d.summary && d.issues.length ? `<p class="small">${esc(d.summary)}</p>` : ""}
        ${s.changes.length ? `<h3>Suggested changes</h3>${changeRows(s.changes)}` : d.issues.length
          ? `<p class="small">No filament setting changes for this one; see the tips below.</p>` : ""}
        ${d.issues.some((i) => i.tips?.length) ? `<h3>Tips</h3><ul class="small">${[...new Set(d.issues.flatMap((i) => i.tips || []))]
          .map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
        <div class="actions">
          ${trial ? `<button class="primary" type="button" id="fix-open">Open the trial profile</button>`
            : s.changes.length ? `<button class="primary" type="button" id="fix-trial">Make a trial profile with these</button>` : ""}
        </div>
        ${trial ? `<p class="small muted">Made <b>${esc(trial.name)}</b>. Print with it, then tell it whether it's better.</p>` : ""}`;
      const open = (id) => { $("#dlg").close(); state.current = id; show("profiles"); };
      $("#fix-open", form)?.addEventListener("click", () => open(trial.id));
      $("#fix-trial", form)?.addEventListener("click", guard(async () => {
        const t = await api(`/api/troubleshoots/${s.id}/trial`, { method: "POST" }); open(t.id);
      }));
      if (!trial) renderProfile(p.id);
    } catch (e) {
      box.innerHTML = `<p class="small" style="color:var(--bad)">${esc(e.message)}</p>`;
    } finally { btn.disabled = false; }
  };
}

function todoPanel(p) {
  const todo = p.confidence.todo || [];
  if (!todo.length) {
    return `<div class="notes" style="border-left:3px solid var(--good)"><b>100% — every key setting is calibrated or proven in a print.</b></div>`;
  }
  const label = (k) => state.meta.fields.find((f) => f.key === k)?.label || k;
  const testTitle = (id) => state.meta.tests.find((t) => t.id === id)?.title || id;
  const confirmable = todo.filter((t) => !t.test).map((t) => t.key);
  return `<div class="notes todo">
    <b>To reach 100%</b> <span class="muted small">(biggest gains first)</span>
    <ul>${todo.map((t) => `<li>
      <span class="gain">+${t.gain}%</span> <b>${esc(label(t.key))}</b>
      <span class="src ${esc(t.source)}">${esc(SRC_LABEL[t.source] || t.source)}</span>
      ${t.test
        ? `<button class="link" data-run-test="${esc(t.test)}">Run ${esc(testTitle(t.test))}</button>
           <span class="muted small">or</span> <button class="link" data-confirm="${esc(t.key)}">it's proven in a print</button>`
        : `<button class="link" data-confirm="${esc(t.key)}">✓ It worked in a print</button>
           ${t.tip ? `<div class="small muted">${esc(t.tip)}</div>` : ""}`}
    </li>`).join("")}</ul>
    ${confirmable.length > 1 ? `<button data-confirm="${esc(confirmable.join(","))}">✓ All of these worked in a real print</button>` : ""}
  </div>`;
}

async function qrDialog(p) {
  const qs = await api("/api/settings/qr");
  let size = 60, style = "two_colour", thickness = 5;
  const form = openDialog(`<h2>Print a QR swatch</h2>
    <p class="small muted">A plate with a QR code on top. Scanning it with a phone opens this profile and lets you save the
      <b>.json</b> or <b>VlottizFF bundle</b>, or send it to someone. The profile is stored in the code itself, so it works anywhere.</p>
    <div class="qr-wrap"><div id="qr-img" class="qr-img"></div>
      <div style="flex:1;min-width:220px">
        <div class="row two">
          <div><label>Style</label><select name="style">
            <option value="two_colour">Two-colour (best scanning)</option>
            <option value="hollow">Hollowed out (one colour)</option></select></div>
          <div><label>Size (mm)</label><input name="size" type="number" min="40" max="120" step="5" value="60"></div>
          <div><label>Thickness (mm)</label><input name="thickness" type="number" min="2" max="10" step="0.5" value="5"></div>
        </div>
        <div><label>Text printed under the code (blank for none)</label><input name="label" maxlength="80"></div>
        <p class="small" id="qr-info"></p>
        <p class="small muted" id="qr-style-tip"></p>
      </div>
    </div>
    <div class="actions"><a class="btn primary" id="qr-dl" href="#">Download .3mf</a>
      <a class="btn" id="qr-open" href="#" target="_blank" rel="noopener">Open the scan page</a>
      <button value="cancel" formnovalidate>Close</button></div>
    <details style="margin-top:14px"><summary class="small">Where the QR code points</summary>
      <p class="small muted">The scan page lives in your GitHub repo's <code>docs</code> folder. Turn it on once: GitHub → your repo →
        Settings → Pages → Source: <b>Deploy from a branch</b>, Branch: <b>main</b>, folder <b>/docs</b> → Save.</p>
      <label>Scan page address</label><input id="qr-base" value="${esc(qs.base_url)}">
      <div class="actions"><button type="button" id="qr-base-save">Save address</button></div>
    </details>`, () => {});
  const tips = {
    two_colour: "Important: in Orca, give the “QR code and name (second colour)” part a dark filament in another toolhead (it's the top 0.6 mm). If both parts use the same filament, the code is invisible.",
    hollow: "One colour: the dark squares are cut almost all the way through, so their shadows make the code. Scan in even light; a lighter filament works best.",
  };
  const refresh = guard(async () => {
    size = Number(form.elements.size.value) || 60;
    style = form.elements.style.value;
    thickness = Number(form.elements.thickness.value) || 5;
    const q = await api(`/api/profiles/${p.id}/qr?size=${size}`);
    if (form.elements.label.dataset.init !== "1") { form.elements.label.value = q.label; form.elements.label.dataset.init = "1"; }
    $("#qr-img", form).innerHTML = q.svg;
    $("#qr-info", form).innerHTML = `${q.modules}×${q.modules} QR, <b>${q.module_mm} mm</b> per square
      ${q.module_mm < 0.8 ? `<span style="color:var(--warn)">: small for a 0.4 nozzle; try ${Math.ceil(0.85 * (q.modules + 4) / 5) * 5} mm</span>` : ""}`;
    $("#qr-style-tip", form).textContent = tips[style];
    $("#qr-dl", form).href = `/api/profiles/${p.id}/swatch.3mf?style=${style}&size=${size}&thickness=${thickness}` +
      `&label=${encodeURIComponent(form.elements.label.value)}`;
    $("#qr-open", form).href = q.url;
  });
  form.oninput = (ev) => { if (ev.target.id !== "qr-base") refresh(); };
  $("#qr-base-save", form).onclick = guard(async () => {
    await api("/api/settings/qr", { method: "PUT", json: { base_url: $("#qr-base", form).value } });
    toast("Saved"); refresh();
  });
  refresh();
}

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
        <div class="small muted" style="margin-top:4px">In Orca: <b>${esc(p.orca_name)}</b></div>
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
      <button id="pf-fix">Fix a print problem</button>
      <button id="pf-qr">Print QR swatch</button>
      <button id="pf-preview">Show JSON</button>
      <button id="pf-dup">Duplicate</button>
      <button class="danger" id="pf-del">Delete</button>
    </div>

    ${trialBanner(p)}
    ${todoPanel(p)}
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

    ${p.troubleshoots?.length ? `<h2>Print problems</h2>${p.troubleshoots.map(problemCard).join("")}` : ""}
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
  $("#pf-qr").onclick = () => qrDialog(p);
  $("#pf-fix").onclick = () => fixDialog(p);
  $$("[data-trial]", el).forEach((b) => (b.onclick = guard(async () => {
    const result = b.dataset.trial;
    if (result === "not_better" && !confirm("Delete this trial profile? The original stays as it was.")) return;
    const r = await api(`/api/profiles/${p.id}/trial/result`, { json: { result } });
    toast(result === "better" ? "Changes copied into the original and marked proven in a print"
      : result === "keep" ? "Kept as its own profile" : "Trial deleted; the original is unchanged");
    state.current = r.id || null;
    loadProfiles();
  })));
  $$("[data-open-profile]", el).forEach((b) => (b.onclick = () => {
    state.current = Number(b.dataset.openProfile); loadProfiles();
  }));
  $$("[data-run-test]", el).forEach((b) => (b.onclick = () => {
    state.calProfile = p.id; state.calTest = b.dataset.runTest; show("calibrate");
  }));
  $$("[data-confirm]", el).forEach((b) => (b.onclick = guard(async () => {
    const keys = b.dataset.confirm.split(",");
    await api(`/api/profiles/${p.id}/confirm`, { json: { keys } });
    toast("Marked as proven in a print");
    loadProfiles();
  })));
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
});

// "remove entry" on calibration results: one handler for every page that shows them.
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-delcal]");
  if (!b) return;
  ev.preventDefault();
  const applied = b.dataset.applied === "1";
  const refresh = () => {
    if ($("#view-calibrate").classList.contains("active")) renderCalTest();
    else if (state.current) renderProfile(state.current);
  };
  openDialog(`<h2>Remove this result?</h2>
    ${applied ? `<p>This result changed the profile. You can undo that change too, or keep the new value and just remove the log entry.</p>
      <p class="small muted">Undo only touches the values this calibration set, and skips any you've edited again since.</p>`
      : `<p>This result was only logged, so the profile won't change.</p>`}
    <div class="actions">
      ${applied ? `<button class="primary" value="undo">Remove and undo the change</button>` : ""}
      <button class="${applied ? "" : "primary"}" value="remove">${applied ? "Just remove the entry" : "Remove"}</button>
      <button value="cancel" formnovalidate>Cancel</button></div>`,
  async (_fd, _form, choice) => {
    const undo = choice === "undo";
    const r = await api(`/api/calibrations/${b.dataset.delcal}?undo=${undo}`, { method: "DELETE" });
    const names = (keys) => keys.map((k) => state.meta.fields.find((f) => f.key === k)?.label || k).join(", ");
    toast(undo ? (r.reverted.length ? `Removed and undid: ${names(r.reverted)}` : "Removed (nothing left to undo)")
      + (r.kept.length ? `. Kept your later edits to ${names(r.kept)}` : "") : "Entry removed");
    refresh();
  });
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
    <button type="button" class="link small" data-delcal="${c.id}" data-applied="${c.applied ? 1 : 0}">remove entry</button>
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
    <p>Writes <b>${esc(p.orca_name || p.name)}</b> into Orca's user filament folder. If a preset with this name already exists it is replaced
    (the old one is in your Orca snapshot if you took one).</p>
    <label>Orca account folder</label>
    <select name="dir">${dirs.map((d) => `<option value="${esc(d)}" ${d === state.meta.orca_active_dir ? "selected" : ""}>${esc(d)}${
      d === state.meta.orca_active_dir ? "  (the one Orca is using now)" : ""}</option>`).join("")}</select>
    <p class="small muted">Orca uses "default" when you're not logged in, and your account's folder when you are. The app picks the one
    Orca is using. <b>Close Orca first</b>, or restart it afterwards: it only reads this folder when it starts.</p>
    ${p.printer?.orca_printer ? `<p class="small muted">It will show up when <b>${esc(p.printer.orca_printer)}</b> is the selected printer.</p>`
      : `<p class="small" style="color:var(--warn)">This profile has no printer, so Orca may not know which printer it's for. Pick one on the profile page.</p>`}
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

const HEIGHT_DIAGRAM = `<svg viewBox="0 0 220 170" class="diagram" role="img" aria-label="Measure from the bed up to where the change happens">
  <rect x="10" y="150" width="200" height="10" rx="2" fill="var(--line)"/>
  <text x="110" y="168" font-size="9" text-anchor="middle" fill="var(--muted)">bed side / bottom of print</text>
  <rect x="70" y="20" width="60" height="130" fill="var(--panel-2)" stroke="var(--muted)"/>
  ${[...Array(12).keys()].map((i) => `<line x1="70" x2="130" y1="${30 + i * 10}" y2="${30 + i * 10}" stroke="var(--line)"/>`).join("")}
  <rect x="70" y="66" width="60" height="10" fill="var(--accent)" opacity=".35"/>
  <line x1="62" x2="134" y1="71" y2="71" stroke="var(--accent)" stroke-width="2" stroke-dasharray="4 3"/>
  <text x="138" y="68" font-size="10" fill="var(--accent)">the change</text>
  <text x="138" y="80" font-size="10" fill="var(--accent)">happens here</text>
  <line x1="40" x2="40" y1="148" y2="73" stroke="var(--text)" stroke-width="1.5" marker-end="url(#arr)" marker-start="url(#arr)"/>
  <text x="34" y="115" font-size="10" fill="var(--text)" text-anchor="middle" transform="rotate(-90 34 115)">measure this</text>
  <defs><marker id="arr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M0 0L10 5L0 10z" fill="var(--text)"/></marker></defs>
</svg>`;

const renderCalTest = guard(async () => {
  const p = await api(`/api/profiles/${state.calProfile}`);
  const tests = state.meta.tests;
  const done = new Set(p.calibrations.filter((c) => c.applied || c.test === "vfa").map((c) => c.test));
  if (!state.calTest) state.calTest = (tests.find((t) => !done.has(t.id) && !t.alt) || tests[0]).id;
  const t = tests.find((x) => x.id === state.calTest);
  const cur = t.setting ? p.settings[t.setting] : null;
  const label = state.meta.fields.find((f) => f.key === t.setting)?.label || t.setting;

  const inputHtml = (i) => {
    const show = i.show_if ? `data-show-if="${esc(i.show_if[0])}=${esc(i.show_if[1])}"` : "";
    if (i.type === "choice") {
      return `<div ${show}><label>${esc(i.label)}</label><select name="${esc(i.key)}">${i.options.map(([v, l]) =>
        `<option value="${esc(v)}" ${v === i.default ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`;
    }
    // Plain text box: accepts any decimal, negatives and comma decimals (0,04). Checked by the server.
    return `<div ${show}><label>${esc(i.label)}</label><input type="text" inputmode="decimal" autocomplete="off"
      name="${esc(i.key)}" value="${esc(i.default ?? "")}" placeholder="${esc(i.placeholder ?? "")}"></div>`;
  };

  $("#cal-body").innerHTML = `
    <p class="muted">Suggested order: temperature → flow → pressure advance → retraction → max flow. Export to Orca between steps so each
    test prints with the latest values.</p>
    <div class="steps">${tests.map((x) => `<div class="step ${x.id === t.id ? "active" : ""} ${done.has(x.id) ? "done" : ""}" data-test="${x.id}">
      <div class="n">${x.alt ? esc(x.alt) : "Step " + x.order}</div><div>${esc(x.title)}</div></div>`).join("")}</div>
    <div class="detail">
      <h2 style="margin-top:0">${esc(t.title)}</h2>
      ${t.setting ? `<p class="small muted">Current ${esc(label)}: <b>${esc(cur ?? "not set")}</b>
        ${p.sources[t.setting] ? `<span class="src ${esc(p.sources[t.setting])}">${esc(SRC_LABEL[p.sources[t.setting]])}</span>` : ""}</p>` : ""}

      <div class="guide">
        <div class="gstep"><span class="num">1</span><div><b>Print it</b>
          <p>In OrcaSlicer: <b>${esc(t.orca_menu)}</b>, with this filament profile selected.</p>
          <ul>${t.setup.map((h) => `<li>${esc(h)}</li>`).join("")}</ul></div></div>
        <div class="gstep"><span class="num">2</span><div><b>What you'll get</b><p>${esc(t.what_you_get)}</p></div></div>
        <div class="gstep"><span class="num">3</span><div><b>How to judge or measure it</b>
          <ul>${t.measure.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>
          ${t.measure_height ? `<div class="diagram-wrap">${HEIGHT_DIAGRAM}
            <p class="small muted">Put one caliper jaw on the bottom of the print and the other at the point where the change happens.
            Measure from the bottom of the print, not the top. A ruler works if you don't have calipers.</p></div>` : ""}</div></div>
        <div class="gstep"><span class="num">4</span><div><b>What to type in</b><p>${esc(t.enter)}</p>
          <p class="example"><b>Example:</b> ${esc(t.example)}</p></div></div>
      </div>

      <form id="cal-form" novalidate>
        <div class="row">${t.inputs.map(inputHtml).join("")}</div>
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
  const visible = (i) => !i.show_if || form.elements[i.show_if[0]].value === i.show_if[1];
  const syncVisible = () => $$("[data-show-if]", form).forEach((el) => {
    const [k, v] = el.dataset.showIf.split("=");
    el.style.display = form.elements[k].value === v ? "" : "none";
  });
  const readInputs = () => Object.fromEntries(t.inputs.filter(visible).map((i) => [i.key, form.elements[i.key].value.trim()]));
  const needsInput = () => t.inputs.filter(visible).some((i) => i.default === undefined && !form.elements[i.key].value.trim());
  const box = $("#cal-result", form);  // this step's own box, so a late reply can't land on the next step
  const preview = async () => {
    if (!form.isConnected) return;
    if (needsInput()) {
      box.className = "result muted"; box.textContent = "Enter your reading to see the new value."; return;
    }
    try {
      const r = await api(`/api/profiles/${p.id}/calibrations/preview`, { json: { test: t.id, inputs: readInputs() } });
      box.className = "result";
      box.innerHTML = r.setting
        ? `New ${esc(label)}: <b>${esc(r.new)}</b> <span class="muted">(was ${esc(r.old ?? "not set")})</span><br><span class="small muted">${esc(r.explanation)}</span>`
        : esc(r.explanation);
    } catch (e) { box.className = "result"; box.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  };
  let timer;
  const onEdit = (ev) => {
    if (ev.target.name === "notes" || ev.target.type === "file" || ev.target.type === "checkbox") return;
    syncVisible();
    clearTimeout(timer);
    timer = setTimeout(preview, 250);
  };
  form.oninput = onEdit;
  form.onchange = onEdit;
  syncVisible();
  form.elements.photos.addEventListener("change", () => {
    $("#cal-thumbs").innerHTML = [...form.elements.photos.files].map((f) =>
      `<img src="${URL.createObjectURL(f)}" alt="">`).join("");
  });
  form.onsubmit = guard(async (ev) => {
    ev.preventDefault();
    clearTimeout(timer);
    if (needsInput() && !form.elements.unsure.checked) { toast("Fill in your reading first", true); return; }
    const fd = new FormData();
    fd.append("test", t.id);
    fd.append("inputs", JSON.stringify(readInputs()));
    fd.append("notes", form.elements.notes.value);
    fd.append("apply", form.elements.unsure.checked ? "false" : "true");
    for (const f of form.elements.photos.files) fd.append("photos", f);
    await api(`/api/profiles/${p.id}/calibrations`, { method: "POST", body: fd });
    toast(form.elements.unsure.checked || !t.setting ? "Logged (profile unchanged)"
      : "Saved and applied. Export to Orca before the next test.");
    const next = tests.find((x) => x.order > t.order && !x.alt);
    state.calTest = next ? next.id : t.id;
    renderCalTest();
  });
  if (!needsInput()) preview();
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
        </dl>
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
    <p class="small muted">"Orca printer preset name" must match the printer's name in Orca exactly (e.g. Snapmaker U1 (0.4 nozzle));
      exported filaments are made for that printer.</p>
    <div style="margin-top:10px"><label>Notes</label><textarea name="notes" rows="2">${esc(p.data.notes ?? "")}</textarea></div>
    <div class="actions"><button class="primary" value="save">Save</button><button value="cancel" formnovalidate>Cancel</button></div>`,
  async (fd) => {
    const data = { ...p.data };
    for (const [k, , t] of PRINTER_FIELDS) {
      const v = fd.get(k);
      data[k] = v === "" ? null : t === "number" ? Number(v) : v;
    }
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
  const group = await api("/api/settings/orca-group");
  await loadPrinters();
  $("#backup-body").innerHTML = `
    <div class="cards wide">
      <div class="card" style="grid-column:1/-1">
        <div class="title">Your group in Orca</div>
        <p class="small muted">Presets exported from this app are grouped under your own name, so they're easy to find in Orca's filament list.</p>
        <form id="group-form">
          <div class="row">
            <div><label>Group name</label><input name="name" value="${esc(group.name)}" maxlength="40"></div>
          </div>
          <label style="display:flex;gap:8px;align-items:center;color:var(--text)"><input type="checkbox" name="prefix_names"
            ${group.prefix_names ? "checked" : ""}> Start preset names with it, so they sort together in Orca's filament list</label>
          <label style="display:flex;gap:8px;align-items:center;color:var(--text);margin-top:6px"><input type="checkbox" name="as_vendor"
            ${group.as_vendor ? "checked" : ""}> Use it as the vendor (Orca groups presets by vendor when you add or manage filaments)</label>
          <p class="small" style="margin-top:8px">Example: <b id="group-example"></b></p>
          <p class="small muted">The real brand stays in this app and in the preset name. Changing this renames presets the next time you
          export or install them; the old copies stay in Orca until you delete them there.</p>
          <div class="actions"><button type="submit">Save</button>
            <a class="btn primary" href="/api/export-bundle">Download ${esc(group.name || "Filament Forge")} bundle</a></div>
          <p class="small muted" style="margin-top:8px"><b>To add it to Orca:</b> File → Import → Import Configs…, pick the
          <code>.orca_filament</code> file. Your filaments then appear in the filament dropdown under <b>Bundle presets →
          ${esc(group.name || "Filament Forge")}</b>, like OrcaGen. Import a newer bundle the same way to update it.</p>
        </form>
      </div>
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
        <p class="small muted" style="margin-top:10px">Import Orca filament presets (.json, several at once, or a .zip). You can
          also drag files anywhere onto the app.</p>
        <input type="file" id="imp-file" accept=".json,.zip" multiple>
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
    toast(`Imported ${p.name}: ${p.import.new_profile ? "new profile" : "updated existing profile"}`);
    state.current = p.id;
    show("profiles");
  })));
  $("#imp-file").onchange = guard(async (ev) => importPresets(ev.target.files));
  const gf = $("#group-form");
  const example = () => {
    const n = gf.elements.name.value.trim();
    $("#group-example").textContent = (gf.elements.prefix_names.checked && n ? n + " " : "") +
      "Giantarm PLA Silk Silver @Snapmaker U1" + (gf.elements.as_vendor.checked && n ? `  (vendor: ${n})` : "  (vendor: Giantarm)");
  };
  gf.oninput = example;
  example();
  gf.onsubmit = guard(async (ev) => {
    ev.preventDefault();
    await api("/api/settings/orca-group", { method: "PUT", json: {
      name: gf.elements.name.value, prefix_names: gf.elements.prefix_names.checked, as_vendor: gf.elements.as_vendor.checked } });
    toast("Saved. Export or install your presets again to update them in Orca.");
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

/* ======================= AI ======================= */
const MODELS = [
  ["qwen2.5vl:7b", "Reads text and photos. ~6 GB. Recommended."],
  ["gemma3:4b", "Reads text and photos. ~3 GB. Lighter, a bit less accurate."],
  ["llama3.1:8b", "Text only. ~5 GB."],
  ["qwen2.5:7b", "Text only. ~5 GB."],
];
const BUSY = new Set(["checking", "downloading", "verifying", "extracting", "starting", "pulling"]);

function aiDot(st) {
  const d = $("#ai-dot");
  d.className = "dot " + (st.state === "ready" ? "ready" : st.state === "error" ? "error" : BUSY.has(st.state) ? "busy" : "");
}

async function pollAI() {
  clearTimeout(pollAI._t);
  const st = await api("/api/ai/status").catch(() => null);
  if (!st) return;
  aiDot(st);
  if ($("#view-ai").classList.contains("active")) drawAIStatus(st);
  if (BUSY.has(st.state) || st.busy) pollAI._t = setTimeout(pollAI, 1000);
}

function drawAIStatus(st) {
  const box = $("#ai-status");
  if (!box) return;
  const pct = st.progress != null ? Math.round(st.progress * 100) : null;
  box.innerHTML = `
    <div class="title">${st.state === "ready" ? "✅ Ready" : st.state === "error" ? "⚠️ Problem" : BUSY.has(st.state) ? "Working…" : "Off"}</div>
    <p>${esc(st.message)}</p>
    ${BUSY.has(st.state) ? `<div class="progress ${pct == null ? "indeterminate" : ""}"><div style="width:${pct ?? 0}%"></div></div>
      <p class="small muted">${pct != null ? pct + "% " : ""}${esc(st.detail || "")}</p>` : ""}
    <dl class="kv small">
      <dt>Engine</dt><dd>${st.running ? `running at ${esc(st.url)}` : "not running"}</dd>
      ${st.gpu ? `<dt>Graphics</dt><dd>${esc(st.gpu)}</dd>` : ""}
      <dt>Disk used</dt><dd>${st.disk_gb} GB</dd>
    </dl>
    <div class="actions">
      ${st.running || BUSY.has(st.state)
        ? `<button id="ai-stop">${BUSY.has(st.state) ? "Cancel" : "Stop"}</button>`
        : `<button class="primary" id="ai-start">${st.settings.mode === "bundled" && !st.installed ? "Download & start" : "Start"}</button>`}
      ${st.disk_gb > 0 ? `<button class="danger" id="ai-remove">Remove AI files</button>` : ""}
    </div>`;
  $("#ai-start")?.addEventListener("click", guard(async () => { await api("/api/ai/start", { method: "POST" }); pollAI(); }));
  $("#ai-stop")?.addEventListener("click", guard(async () => {
    await api("/api/ai/settings", { method: "PUT", json: { enabled: false } }); pollAI(); }));
  $("#ai-remove")?.addEventListener("click", guard(async () => {
    if (!confirm(`Delete the AI engine and models (${st.disk_gb} GB)? You can download them again later.`)) return;
    await api("/api/ai/uninstall", { method: "POST" }); pollAI(); }));
}

async function renderAI() {
  const st = await api("/api/ai/status");
  const s = st.settings;
  const sel = (name, opts) => `<select name="${name}">${opts.map(([v, l]) =>
    `<option value="${v}" ${s[name] === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  $("#ai-body").innerHTML = `
    <div class="cards wide">
      <div class="card" id="ai-status"></div>
      <div class="card">
        <div class="title">How it works</div>
        <ul class="small">
          <li>Runs a small AI model on <b>your</b> computer with Ollama. Nothing is sent to an AI company.</li>
          <li>It only runs while this app is open, and is stopped when you close the app.</li>
          <li>First start downloads the engine (~1.6 GB) and a model (3–6 GB). After that it starts in seconds.</li>
          <li>With an NVIDIA or AMD graphics card a page takes a few seconds. Without one, expect 30–90 seconds.</li>
        </ul>
      </div>
    </div>
    <h2>Settings</h2>
    <form id="ai-form" class="card">
      <div class="row">
        <div><label>How to run it</label>${sel("mode", [["bundled", "Built in (recommended)"], ["docker", "Docker container"],
          ["external", "My own Ollama"]])}</div>
        <div><label>Graphics card</label>${sel("gpu", [["auto", "Detect automatically"], ["nvidia", "NVIDIA"], ["amd", "AMD"], ["cpu", "None / CPU only"]])}</div>
        <div><label>Model</label><input name="model" list="ai-models" value="${esc(s.model)}"></div>
        <div class="ext"><label>Your Ollama address</label><input name="external_url" value="${esc(s.external_url)}"></div>
      </div>
      <datalist id="ai-models">${MODELS.map(([m, d]) => `<option value="${m}">${esc(d)}</option>`).join("")}</datalist>
      <ul class="small muted">${MODELS.map(([m, d]) => `<li><code>${m}</code> — ${esc(d)}</li>`).join("")}</ul>
      <p class="small muted mode-note"></p>
      <div class="actions"><button class="primary" type="submit">Save settings</button></div>
    </form>`;
  const form = $("#ai-form");
  const notes = {
    bundled: "Downloads Ollama into this app's data folder and runs it in the background. No admin rights or Docker needed.",
    docker: "Needs Docker Desktop running. GPU works with NVIDIA cards only. The container is removed when the app closes; models are kept.",
    external: "Uses an Ollama you installed yourself. This app won't start or stop it.",
  };
  const sync = () => {
    $(".ext", form).style.display = form.elements.mode.value === "external" ? "" : "none";
    $(".mode-note", form).textContent = notes[form.elements.mode.value];
  };
  form.elements.mode.onchange = sync;
  sync();
  form.onsubmit = guard(async (ev) => {
    ev.preventDefault();
    const body = Object.fromEntries(new FormData(form).entries());
    await api("/api/ai/settings", { method: "PUT", json: body });
    toast("Saved");
    pollAI();
  });
  drawAIStatus(st);
  aiDot(st);
  if (BUSY.has(st.state) || st.busy) pollAI();
}

/* ---------- boot ---------- */
(async () => {
  try {
    state.meta = await api("/api/meta");
    await loadPrinters();
    let view = "filaments";
    try { view = localStorage.getItem("ff-view") || view; } catch {}
    show(view);
    pollAI();
  } catch (e) { toast("Couldn't reach the app server: " + e.message, true); }
})();

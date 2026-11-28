/* Filament Forge QR link decoder. Rebuilds the OrcaSlicer filament preset from
 * the data packed into the link (see forge/swatch.py). Works in the browser
 * (window.FF) and in Node (module.exports) so tests can check it matches the app. */
(function (root) {
  "use strict";
  // Frozen order (v1): printed swatches depend on it. Must match orca.QR_FIELDS_V1.
  const FIELDS = [
    "filament_type", "filament_vendor", "default_filament_colour", "filament_diameter", "filament_density",
    "filament_cost", "nozzle_temperature", "nozzle_temperature_initial_layer", "nozzle_temperature_range_low",
    "nozzle_temperature_range_high", "hot_plate_temp", "hot_plate_temp_initial_layer", "textured_plate_temp",
    "textured_plate_temp_initial_layer", "temperature_vitrification", "filament_flow_ratio",
    "filament_max_volumetric_speed", "enable_pressure_advance", "pressure_advance", "filament_retraction_length",
    "filament_retraction_speed", "fan_min_speed", "fan_max_speed", "overhang_fan_speed",
    "close_fan_the_first_x_layers", "fan_cooling_layer_time", "slow_down_layer_time",
  ];
  const PRESET_VERSION = "2.0.0.0";

  const dec = (s) => decodeURIComponent(s.replace(/\+/g, " "));

  function decode(fragment) {
    const parts = String(fragment || "").replace(/^#/, "").split("*").map(dec);
    if (parts[0] !== "1") throw new Error("This isn't a Filament Forge swatch link (or it's from a newer version).");
    if (parts.length !== 5 + FIELDS.length + 1) throw new Error("This link is incomplete. Try scanning again.");
    const [, name, group, inherits, printer] = parts;
    const settings = {};
    FIELDS.forEach((k, i) => { if (parts[5 + i] !== "") settings[k] = parts[5 + i]; });
    return { name, group, inherits, printer, settings, sources: parts[parts.length - 1] };
  }

  function notes(d) {
    const lines = ["Made with Filament Forge."];
    const pick = (code) => FIELDS.filter((k, i) => d.sources[i] === code && k in d.settings);
    const cal = pick("c");
    if (cal.length) lines.push("Calibrated: " + cal.map((k) => `${k} ${d.settings[k]}`).join(", "));
    const tested = pick("t");
    if (tested.length) lines.push("Proven in a print: " + tested.join(", "));
    lines.push(`[ff1:${d.sources}]`);
    return lines.join("\n");
  }

  function presetName(d, inBundle) {
    const g = (d.group || "").trim();
    if (inBundle || !g || d.name.toLowerCase().startsWith(g.toLowerCase() + " ")) return d.name;
    return `${g} ${d.name}`;
  }

  /* Same shape as forge/orca.py to_preset(). */
  function buildPreset(d, inBundle) {
    const name = presetName(d, inBundle);
    const p = { type: "filament", name, inherits: d.inherits, from: "User", instantiation: "true",
      version: PRESET_VERSION, filament_settings_id: [name] };
    if (d.printer) p.compatible_printers = [d.printer];
    p.filament_notes = [notes(d)];
    Object.keys(d.settings).sort().forEach((k) => { p[k] = [d.settings[k]]; });
    return p;
  }

  function safeFilename(name) {
    return name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/^[ .]+|[ .]+$/g, "") || "filament";
  }

  function bundleId(name) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "filament-forge";
  }

  // --- tiny zip writer (stored, no compression) for the .orca_filament bundle
  const CRC = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t;
  })();
  function crc32(bytes) { let c = 0xffffffff; for (const b of bytes) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }

  function zip(files) {  // files: [{name, data: Uint8Array}]
    const enc = new TextEncoder(), chunks = [], central = [];
    let offset = 0;
    const u16 = (v) => [v & 0xff, (v >>> 8) & 0xff];
    const u32 = (v) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];
    for (const f of files) {
      const nameBytes = enc.encode(f.name), crc = crc32(f.data), size = f.data.length;
      const local = [...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21),
        ...u32(crc), ...u32(size), ...u32(size), ...u16(nameBytes.length), ...u16(0)];
      chunks.push(new Uint8Array(local), nameBytes, f.data);
      central.push(new Uint8Array([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0),
        ...u16(0x21), ...u32(crc), ...u32(size), ...u32(size), ...u16(nameBytes.length), ...u16(0), ...u16(0),
        ...u16(0), ...u16(0), ...u32(0), ...u32(offset)]), nameBytes);
      offset += local.length + nameBytes.length + size;
    }
    const cdSize = central.reduce((n, c) => n + c.length, 0);
    const end = new Uint8Array([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length),
      ...u32(cdSize), ...u32(offset), ...u16(0)]);
    return new Blob([...chunks, ...central, end], { type: "application/zip" });
  }

  /* Same manifest as forge/orca.py make_bundle(). */
  function bundleFiles(d) {
    const enc = new TextEncoder();
    const group = (d.group || "").trim() || "Filament Forge";
    const preset = buildPreset(d, true);
    const fname = safeFilename(preset.name) + ".json";
    const now = new Date(), pad = (n) => String(n).padStart(2, "0");
    const manifest = { id: bundleId(group), name: group,
      version: `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())}.${pad(now.getHours())}${pad(now.getMinutes())}`,
      description: `${group} filament presets from Filament Forge`, author: "",
      updated_time: Math.floor(now.getTime() / 1000), print_presets: [], filament_presets: [fname], printer_presets: [] };
    return { group, files: [
      { name: "bundle_structure.json", data: enc.encode(JSON.stringify(manifest, null, 4)) },
      { name: fname, data: enc.encode(JSON.stringify(preset, null, 4)) },
    ] };
  }

  const api = { FIELDS, decode, buildPreset, presetName, safeFilename, bundleFiles, zip, crc32 };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FF = api;
})(typeof window !== "undefined" ? window : globalThis);

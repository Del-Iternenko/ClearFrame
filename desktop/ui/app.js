"use strict";
// ClearFrame UI: pages are built from small declarative helpers; every string goes through t().
const S = { settings: {}, schema: {}, state: {}, lang: "en", page: "home", recording: null, sysLang: "en", engines: {} };
const $ = (sel, el = document) => el.querySelector(sel);
const api = () => window.pywebview && window.pywebview.api;

// ---------- i18n ----------
function t(key, vars) {
  let s = (I18N[S.lang] && I18N[S.lang][key]) || I18N.en[key] || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
}
function applyLang() {
  const pick = S.settings.language === "auto" ? S.sysLang : S.settings.language;
  S.lang = I18N[pick] ? pick : "en";
  document.documentElement.lang = S.lang;
  document.documentElement.dir = RTL.includes(S.lang) ? "rtl" : "ltr";
}

// ---------- DOM helpers ----------
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round"); svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "i " + cls); svg.innerHTML = ICONS[name] || "";
  return svg;
}

// ---------- settings plumbing ----------
async function set(key, value) {
  S.settings[key] = value;
  const r = api() ? await api().set_setting(key, value) : { ok: true };
  if (!r.ok) console.warn(r.error);
  if (["language", "theme", "accent", "ui_scale", "reduce_motion"].includes(key)) applyLook();
  render();
}
function applyLook() {
  applyLang();
  const root = document.documentElement;
  const theme = S.settings.theme === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : S.settings.theme;
  root.dataset.theme = theme;
  root.style.setProperty("--accent", S.settings.accent);
  root.classList.toggle("reduce-motion", !!S.settings.reduce_motion);
  document.body.style.zoom = (S.settings.ui_scale || 100) / 100;
}

// ---------- controls ----------
function row({ icon: ic, title, desc, control, badge, disabled }) {
  return h("div", { class: "row" + (disabled ? " disabled" : "") },
    h("div", { class: "ico" }, icon(ic)),
    h("div", { class: "txt" }, h("div", { class: "title" }, t(title), badge ? h("span", { class: "badge " + (badge.cls || "") }, t(badge.key)) : null),
      desc ? h("div", { class: "desc" }, t(desc)) : null),
    h("div", { class: "ctl" }, control));
}
function toggle(key) {
  const input = h("input", { type: "checkbox", role: "switch", "aria-label": key, onchange: e => set(key, e.target.checked) });
  input.checked = !!S.settings[key];
  return h("label", { class: "switch" }, input, h("span"));
}
function seg(key, options, label) {
  return h("div", { class: "seg", role: "group" }, options.map(o =>
    h("button", { "aria-pressed": String(S.settings[key] === o), onclick: () => set(key, o) }, label(o))));
}
function select(key, options, label, disabledOpts = []) {
  const sel = h("select", { "aria-label": key, onchange: e => set(key, isNaN(+e.target.value) || typeof S.settings[key] === "string" ? e.target.value : +e.target.value) },
    options.map(o => { const opt = h("option", { value: o, disabled: disabledOpts.includes(o) }, label(o)); opt.selected = S.settings[key] === o; return opt; }));
  return h("div", { class: "select" }, sel, icon("chevron-down", "sm"));
}
function range(key, min, max, step, fmt = v => v) {
  const out = h("output", {}, fmt(S.settings[key]));
  const input = h("input", { type: "range", min, max, step, value: S.settings[key], "aria-label": key,
    oninput: e => { out.textContent = fmt(+e.target.value); },
    onchange: e => set(key, step < 1 ? +(+e.target.value).toFixed(1) : +e.target.value) });
  return h("div", { class: "range" }, input, out);
}
function pageHead(title, lead) { return [h("h1", {}, t(title)), h("p", { class: "lead" }, t(lead))]; }
function section(title, hint, ...rows) {
  return [h("h2", {}, t(title)), hint ? h("p", { class: "section-hint" }, t(hint)) : null, h("div", { class: "rows" }, ...rows)];
}

// ---------- pages ----------
const PAGES = [
  { id: "home", icon: "house", label: "nav.home" },
  { group: "nav.settings" },
  { id: "upscale", icon: "sparkles", label: "nav.upscale" },
  { id: "capture", icon: "scan-line", label: "nav.capture" },
  { id: "hotkeys", icon: "keyboard", label: "nav.hotkeys" },
  { id: "appearance", icon: "palette", label: "nav.appearance" },
  { id: "system", icon: "settings", label: "nav.system" },
  { spacer: true },
  { id: "about", icon: "info", label: "nav.about" },
];

function statusKey() {
  const st = S.state;
  if (st.status === "paused_battery") return "status.paused_battery";
  if (st.running) return st.auto ? "status.auto" : "status.running";
  return S.settings.auto_fullscreen ? "status.waiting" : "status.idle";
}
function parseInfo(info) {   // "on screen 1280x535 -> real 1280x534 -> RTX VSR x2.02 -> 2588x1080"
  const parts = (info || "").split("->").map(s => s.trim());
  return parts.length >= 4 ? { source: parts[1].replace(/^real /, ""), output: parts[3], scale: parts[2] } : null;
}

function pageHome() {
  const st = S.state, on = st.running, info = parseInfo(st.info);
  const power = h("button", { class: "power" + (on || S.settings.auto_fullscreen ? " on" : ""), "aria-label": "power",
    onclick: () => on ? api().stop() : set("auto_fullscreen", !S.settings.auto_fullscreen) }, icon("power"));
  const sub = on ? (st.target || "") : t(S.settings.auto_fullscreen ? "home.hint_auto" : "home.hint_off");
  const actions = on ? [
    h("button", { class: "btn primary", onclick: () => api().toggle_compare() }, icon("columns-2", "sm"), t(st.comparing ? "home.back" : "home.compare")),
    h("button", { class: "btn", onclick: () => api().stop() }, icon("x", "sm"), t("home.stop")),
  ] : [];
  const SHORT = { rtx_driver: "RTX VSR", nvvfx: "NVIDIA VFX", clearframe: "ClearFrame Neural" };
  const engineName = SHORT[S.settings.engine] || t("engine." + S.settings.engine);
  const modeCard = (id, ic, title, desc, active, onclick) => h("button", { class: "card mode" + (active ? " active" : ""), onclick },
    h("div", { class: "t" }, icon(ic), t(title)), h("div", { class: "d" }, t(desc)));
  return h("div", { class: "page" },
    ...pageHead("home.title", "home.lead"),
    h("div", { class: "card hero" + (on ? " on" : "") }, power,
      h("div", {}, h("div", { class: "state" }, t(statusKey())), h("div", { class: "sub" }, sub), h("div", { class: "actions" }, actions))),
    h("div", { class: "stats" },
      h("div", { class: "card stat" }, h("div", { class: "k" }, icon("film", "sm"), t("home.stat_source")), h("div", { class: "v" }, info ? info.source : t("home.none"))),
      h("div", { class: "card stat" }, h("div", { class: "k" }, icon("maximize", "sm"), t("home.stat_output")), h("div", { class: "v" }, info ? info.output : t("home.none"))),
      h("div", { class: "card stat" }, h("div", { class: "k" }, icon("cpu", "sm"), t("home.stat_engine")), h("div", { class: "v" }, engineName))),
    h("h2", {}, t("home.modes")),
    h("div", { class: "modes" },
      modeCard("auto", "zap", "home.mode_auto", "home.mode_auto_d", S.settings.auto_fullscreen, () => set("auto_fullscreen", !S.settings.auto_fullscreen)),
      modeCard("window", "app-window", "home.mode_window", "home.mode_window_d", on && !st.auto && !/^screen/.test(st.target || ""), () => openPicker("window")),
      modeCard("screen", "monitor", "home.mode_screen", "home.mode_screen_d", on && /^screen/.test(st.target || ""), () => openPicker("screen"))));
}

function pageUpscale() {
  const nvvfx = S.settings.engine === "nvvfx" && S.engines.nvvfx;
  const engines = ["clearframe", "nvvfx", "rtx_driver", "none"];
  const missing = engines.filter(o => S.engines[o] === false);
  // quality / artifact reduction are NVIDIA VFX controls: greyed with another engine, badged when VFX isn't installed
  const vfxBadge = S.engines.nvvfx ? null : { key: "badge.sdk", cls: "warn" };
  return h("div", { class: "page" }, ...pageHead("up.title", "up.lead"),
    ...section("up.s_engine", null,
      row({ icon: "cpu", title: "up.engine", desc: "up.engine_d", control: select("engine", engines, o => t("engine." + o) + (o === "clearframe" ? " · " + t("badge.best") : "") + (missing.includes(o) ? " · " + t("badge.missing") : ""), missing) }),
      row({ icon: "gauge", title: "up.quality", desc: "up.quality_d", disabled: !nvvfx, badge: vfxBadge,
        control: seg("quality", ["low", "medium", "high", "ultra"], o => t("q." + o)) }),
      row({ icon: "wand-sparkles", title: "up.ar", desc: "up.ar_d", disabled: !nvvfx, badge: vfxBadge,
        control: seg("artifact_reduction", ["off", "light", "strong"], o => t("ar." + o)) }),
      row({ icon: "film", title: "up.source", desc: "up.source_d", control: seg("source", ["auto", "360p", "480p", "720p", "1080p"], o => o === "auto" ? t("src.auto") : o) })),
    ...section("up.s_clean", null,
      row({ icon: "layers", title: "up.deband", desc: "up.deband_d", control: toggle("deband") }),
      row({ icon: "sliders-horizontal", title: "up.deband_strength", desc: "up.deband_strength_d", disabled: !S.settings.deband, control: range("deband_strength", 16, 128, 8) }),
      row({ icon: "sparkles", title: "up.grain", desc: "up.grain_d", disabled: !S.settings.deband, control: range("deband_grain", 0, 48, 4) })));
}

function appsEditor() {
  const list = S.settings.auto_apps || [];
  const input = h("input", { class: "input", placeholder: t("cap.app_ph"), list: "exe-suggest",
    onkeydown: e => { if (e.key === "Enter") add(); } });
  const add = () => { const v = input.value.trim(); if (v && !list.includes(v)) set("auto_apps", [...list, v]); };
  const datalist = h("datalist", { id: "exe-suggest" });
  if (api()) api().windows().then(ws => [...new Set(ws.map(w => w.exe).filter(Boolean))].forEach(x => datalist.append(h("option", { value: x }))));
  return h("div", {}, h("div", { style: "display:flex;gap:8px" }, input, datalist, h("button", { class: "btn", onclick: add }, icon("plus", "sm"), t("cap.app_add"))),
    h("div", { class: "chips" }, list.map(a => h("span", { class: "chip" }, a,
      h("button", { "aria-label": "remove " + a, onclick: () => set("auto_apps", list.filter(x => x !== a)) }, icon("x", "sm"))))));
}

function pageCapture() {
  const auto = S.settings.auto_fullscreen, appsOn = S.settings.auto_apps_mode !== "all";
  return h("div", { class: "page" }, ...pageHead("cap.title", "cap.lead"),
    ...section("cap.s_auto", null,
      row({ icon: "zap", title: "cap.auto", desc: "cap.auto_d", control: toggle("auto_fullscreen") }),
      row({ icon: "timer", title: "cap.delay", desc: "cap.delay_d", disabled: !auto, control: range("auto_delay", 0.5, 5, 0.5, v => v.toFixed(1) + " s") }),
      row({ icon: "app-window", title: "cap.apps", desc: "cap.apps_d", disabled: !auto, control: seg("auto_apps_mode", ["video", "all", "only", "except"], o => t("apps." + o)) }),
      appsOn && auto ? h("div", { class: "row" }, h("div", { class: "ico" }, icon("plus")), h("div", { class: "txt" }, appsEditor())) : null),
    ...section("cap.s_output", null,
      row({ icon: "cast", title: "cap.output", desc: "cap.output_d", control: seg("output", ["overlay", "window"], o => t("out." + o)) }),
      row({ icon: "maximize", title: "cap.height", desc: "cap.height_d", disabled: S.settings.output !== "window", control: seg("window_height", [1080, 1440, 2160], o => o + "p") })),
    ...section("cap.s_perf", null,
      row({ icon: "activity", title: "cap.fps", desc: "cap.fps_d", control: seg("max_fps", [30, 60, 120], o => o + " fps") }),
      row({ icon: "battery-low", title: "cap.battery", desc: "cap.battery_d", control: toggle("pause_on_battery") })));
}

// hotkeys: click a shortcut, press the new combination
const KEYMAP = { " ": "Space", PageUp: "PageUp", PageDown: "PageDown", Home: "Home", End: "End", Insert: "Insert", Delete: "Delete" };
function comboFrom(e) {
  const mods = []; if (e.ctrlKey) mods.push("Ctrl"); if (e.altKey) mods.push("Alt"); if (e.shiftKey) mods.push("Shift"); if (e.metaKey) mods.push("Win");
  let key = KEYMAP[e.key] || (/^F\d{1,2}$/.test(e.key) ? e.key : (e.code.startsWith("Key") ? e.code.slice(3) : e.code.startsWith("Digit") ? e.code.slice(5) : null));
  if (!key || ["Control", "Alt", "Shift", "Meta"].includes(e.key)) return { mods, key: null };
  return { mods, key };
}
function hotkeyControl(key) {
  const recording = S.recording === key;
  const err = (S.state.hotkey_errors || []).includes(key);
  const keys = recording ? [h("span", { class: "kbd rec" }, t("hk.press"))] : S.settings[key].split("+").map(k => h("span", { class: "kbd" }, k));
  return h("div", { style: "display:flex;flex-direction:column;align-items:flex-end;gap:4px" },
    h("div", { class: "keys" }, keys, h("button", { class: "btn ghost", "aria-label": "edit", onclick: () => { S.recording = key; render(); } }, icon("keyboard", "sm"))),
    err ? h("div", { class: "err" }, t("hk.taken")) : null);
}
document.addEventListener("keydown", e => {
  if (!S.recording) return;
  e.preventDefault();
  if (e.key === "Escape") { S.recording = null; render(); return; }
  const { mods, key } = comboFrom(e);
  if (!key) return;
  if (!mods.length) { toast(t("hk.need_mod")); return; }
  const k = S.recording; S.recording = null;
  set(k, [...mods, key].join("+"));
});
function pageHotkeys() {
  const r = (k, ic, title, desc) => row({ icon: ic, title, desc, control: hotkeyControl(k) });
  return h("div", { class: "page" }, ...pageHead("hk.title", "hk.lead"),
    h("div", { class: "rows" },
      r("hotkey_window", "app-window", "hk.window", "hk.window_d"), r("hotkey_screen", "monitor", "hk.screen", "hk.screen_d"),
      r("hotkey_auto", "zap", "hk.auto", "hk.auto_d"), r("hotkey_compare", "columns-2", "hk.compare", "hk.compare_d")));
}

function pageAppearance() {
  const langs = ["auto", ...Object.keys(LANG_NAMES)];
  const swatches = h("div", { class: "swatches" }, (S.schema.accent?.options || []).map(c =>
    h("button", { class: "swatch", style: `background:${c}`, "aria-label": c, "aria-pressed": String(S.settings.accent === c), onclick: () => set("accent", c) })));
  return h("div", { class: "page" }, ...pageHead("ap.title", "ap.lead"),
    h("div", { class: "rows" },
      row({ icon: "languages", title: "ap.language", desc: "ap.language_d", control: select("language", langs, o => o === "auto" ? t("lang.auto") + ` (${LANG_NAMES[S.sysLang] || "English"})` : LANG_NAMES[o]) }),
      row({ icon: "sun", title: "ap.theme", desc: "ap.theme_d", control: seg("theme", ["dark", "light", "system"], o => t("theme." + o)) }),
      row({ icon: "palette", title: "ap.accent", desc: "ap.accent_d", control: swatches }),
      row({ icon: "type", title: "ap.scale", desc: "ap.scale_d", control: seg("ui_scale", [90, 100, 110, 125], o => o + "%") }),
      row({ icon: "mouse-pointer-click", title: "ap.motion", desc: "ap.motion_d", control: toggle("reduce_motion") })));
}

function pageSystem() {
  return h("div", { class: "page" }, ...pageHead("sys.title", "sys.lead"),
    h("div", { class: "rows" },
      row({ icon: "rocket", title: "sys.startup", desc: "sys.startup_d", control: toggle("start_with_windows") }),
      row({ icon: "minus", title: "sys.minimized", desc: "sys.minimized_d", control: toggle("start_minimized") }),
      row({ icon: "x", title: "sys.tray", desc: "sys.tray_d", control: toggle("close_to_tray") }),
      row({ icon: "bell", title: "sys.notify", desc: "sys.notify_d", control: toggle("notifications") }),
      row({ icon: "file-text", title: "sys.log", desc: "sys.log_d", control: h("button", { class: "btn", onclick: () => api().open_log() }, icon("folder-open", "sm"), t("sys.open")) }),
      row({ icon: "rotate-ccw", title: "sys.reset", desc: "sys.reset_d", control: h("button", { class: "btn danger", onclick: async () => {
        S.settings = await api().reset_settings(); applyLook(); render(); toast(t("sys.reset_done")); } }, t("sys.reset_btn")) })));
}

function pageAbout() {
  return h("div", { class: "page" }, h("h1", {}, t("about.title")),
    h("div", { class: "card about", style: "margin-top:16px" }, h("div", { class: "logo-xl" }, icon("play")),
      h("div", {}, h("div", { style: "font-size:20px;font-weight:650" }, "ClearFrame ", h("span", { class: "muted", style: "font-size:14px;font-weight:500" }, "v" + S.version)),
        h("div", { class: "muted", style: "margin:4px 0 12px" }, t("about.desc")),
        h("button", { class: "btn", onclick: () => api().open_repo() }, icon("github", "sm"), t("about.repo"), icon("external-link", "sm")))),
    ...section("about.licenses", null, h("div", { class: "row" }, h("div", { class: "ico" }, icon("file-text")), h("div", { class: "txt" }, h("div", { class: "desc" }, t("about.licenses_d"))))));
}

// ---------- picker dialog ----------
async function openPicker(kind) {
  const layer = $("#layer");
  const close = () => { layer.innerHTML = ""; };
  const items = kind === "window" ? await api().windows() : await api().screens();
  const list = h("div", { class: "list" }, items.length ? items.map(it => kind === "window"
      ? h("button", { class: "pick", onclick: () => { api().start_window(it.hwnd); close(); } }, icon("app-window"), h("span", { class: "t" }, it.title), h("span", { class: "m" }, it.exe))
      : h("button", { class: "pick", onclick: () => { api().start_screen(it.index); close(); } }, icon("monitor"),
          h("span", { class: "t" }, t("picker.screen_n", { n: it.index }) + (it.primary ? ` (${t("picker.main")})` : "")), h("span", { class: "m" }, `${it.w}×${it.h}`)))
    : h("div", { class: "muted", style: "padding:12px" }, t("picker.empty")));
  layer.append(h("div", { class: "scrim", onclick: e => { if (e.target === e.currentTarget) close(); } },
    h("div", { class: "card dialog", role: "dialog", "aria-modal": "true" },
      h("h3", {}, t(kind === "window" ? "picker.window" : "picker.screen")), h("div", { class: "muted" }, t(kind === "window" ? "picker.window_d" : "picker.screen_d")),
      list, h("div", { style: "display:flex;justify-content:flex-end;margin-top:16px" }, h("button", { class: "btn", onclick: close }, t("picker.cancel"))))));
}
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#layer").innerHTML && !S.recording) $("#layer").innerHTML = ""; });

let toastTimer;
function toast(msg) {
  document.querySelectorAll(".toast").forEach(x => x.remove());
  document.body.append(h("div", { class: "toast", role: "status" }, icon("check", "sm"), msg));
  clearTimeout(toastTimer); toastTimer = setTimeout(() => document.querySelectorAll(".toast").forEach(x => x.remove()), 2500);
}

// ---------- shell ----------
function renderNav() {
  const nav = $("#nav"); nav.innerHTML = "";
  for (const p of PAGES) {
    if (p.group) { nav.append(h("div", { class: "group" }, t(p.group))); continue; }
    if (p.spacer) { nav.append(h("div", { class: "spacer" })); continue; }
    nav.append(h("button", { "aria-current": S.page === p.id ? "page" : null, onclick: () => { S.page = p.id; S.recording = null; render(); $("#main").scrollTop = 0; } },
      icon(p.icon), h("span", { class: "label" }, t(p.label))));
  }
  const on = S.state.running;
  nav.append(h("div", { class: "status-pill", role: "status" }, h("span", { class: "dot" + (on ? " on" : "") }), h("span", {}, t(statusKey()))));
}
const RENDER = { home: pageHome, upscale: pageUpscale, capture: pageCapture, hotkeys: pageHotkeys, appearance: pageAppearance, system: pageSystem, about: pageAbout };
function render() {
  renderNav();
  const main = $("#main"), scroll = main.scrollTop;
  main.replaceChildren(RENDER[S.page]());
  main.scrollTop = scroll;
}

// Python pushes state changes here
window.cfPush = data => {
  S.state = data.state;
  const look = ["language", "theme", "accent", "ui_scale", "reduce_motion"].some(k => data.settings[k] !== S.settings[k]);
  S.settings = data.settings;
  if (look) applyLook();
  if (!S.recording) render(); else renderNav();
};

async function boot() {
  document.querySelectorAll("[data-icon]").forEach(el => el.append(icon(el.dataset.icon, "sm")));
  $("#btn-min").onclick = () => api().minimize();
  $("#btn-max").onclick = () => api().toggle_maximize();
  $("#btn-close").onclick = () => api().close();
  const b = await api().bootstrap();
  Object.assign(S, { settings: b.settings, schema: b.schema, state: b.state, sysLang: b.system_language, version: b.version, engines: b.engines || {} });
  $("#ver").textContent = "v" + b.version;
  applyLook(); render();
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => S.settings.theme === "system" && applyLook());
  setInterval(async () => { const st = await api().state(); if (JSON.stringify(st) !== JSON.stringify(S.state)) { S.state = st; if (!S.recording) render(); } }, 2000);
}
window.addEventListener("pywebviewready", boot);

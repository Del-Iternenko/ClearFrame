// UI screenshots through the WebView2 DevTools port (app started with
// WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9223).
// Changes language/page/theme only inside the page - nothing is saved.
//   node desktop/uishots.js <outdir> lang:page[:theme] ...
const fs = require("fs"), path = require("path");
const [outDir, ...shots] = process.argv.slice(2);
(async () => {
  const targets = await (await fetch("http://127.0.0.1:9223/json")).json();
  const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));
  let id = 0; const pending = new Map();
  ws.addEventListener("message", e => { const m = JSON.parse(e.data); if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const cmd = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async e => (await cmd("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true })).result?.result?.value;
  fs.mkdirSync(outDir, { recursive: true });
  for (const spec of shots) {
    const [lang, page, theme] = spec.split(":");
    const ok = await evalJs(`(() => { S.settings.language = ${JSON.stringify(lang)}; ${theme ? `S.settings.theme = ${JSON.stringify(theme)};` : ""}
      S.page = ${JSON.stringify(page)}; applyLook(); render(); return document.documentElement.dir + " " + document.documentElement.lang; })()`);
    await new Promise(r => setTimeout(r, 400));
    const shot = await cmd("Page.captureScreenshot", { format: "png" });
    const file = path.join(outDir, `${lang}-${page}${theme ? "-" + theme : ""}.png`);
    fs.writeFileSync(file, Buffer.from(shot.result.data, "base64"));
    const overflow = await evalJs(`[...document.querySelectorAll('.row .title, .seg button, .mode .t, nav button .label')].filter(e => e.scrollWidth > e.clientWidth + 1 || e.getBoundingClientRect().right > innerWidth).length`);
    console.log(spec, "->", ok, "| clipped elements:", overflow, "|", file);
  }
  await evalJs(`(async () => { const b = await pywebview.api.bootstrap(); S.settings = b.settings; S.page = "home"; applyLook(); render(); })()`);
  ws.close();
})().catch(e => { console.error(e); process.exit(1); });

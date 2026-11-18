// fledge-lite shell: owns one session, one Web Worker (Pyodide + Fledge core),
// and relays API requests from the service worker to that Web Worker.
// It also turns the URL into a configuration, and a configuration back into a URL.

const session = Math.random().toString(36).slice(2, 10);
const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const errorEl = $("error");
const query = new URLSearchParams(location.search);

const worker = new Worker("worker.js", { type: "module" });
const pending = new Map();      // request id -> MessagePort back to the service worker
const exports = new Map();      // export id -> resolve
let nextId = 1;
let workerReady;
const ready = new Promise((r) => (workerReady = r));

worker.onmessage = (ev) => {
  const m = ev.data;
  if (m.type === "status") statusEl.textContent = m.text;
  else if (m.type === "error") fail(m.text);
  else if (m.type === "ready") {
    if (m.setupLog && m.setupLog.length) console.table(m.setupLog);
    workerReady();
  } else if (m.type === "response") {
    const port = pending.get(m.id);
    pending.delete(m.id);
    port.postMessage({ status: m.status, headers: m.headers, body: m.body }, [m.body]);
  } else if (m.type === "exported") {
    exports.get(m.id)(m.json);
    exports.delete(m.id);
  }
};

function fail(text) {
  statusEl.textContent = "Failed to start";
  errorEl.textContent = text;
}

// ------------------------------------------------- setup <-> URL fragment
// #setup= holds the JSON list of REST calls, deflated and base64url encoded.
// The fragment never leaves the browser, so a link can carry a whole config.
const b64url = {
  encode: (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
  decode: (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0)),
};

async function encodeSetup(steps) {
  const stream = new Blob([JSON.stringify(steps)]).stream().pipeThrough(new CompressionStream("deflate-raw"));
  return b64url.encode(new Uint8Array(await new Response(stream).arrayBuffer()));
}

async function decodeSetup(s) {
  const text = decodeURIComponent(s).trim();
  if (text.startsWith("[")) return JSON.parse(text);   // plain JSON is fine too, for hand-written links
  const stream = new Blob([b64url.decode(text)]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return JSON.parse(await new Response(stream).text());
}

// ---------------------------------------------------------- URL -> config
async function fetchText(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.text();
}

// Replace gist file names used as values (e.g. a CSV url) with the file's raw URL.
function substitute(value, files) {
  if (typeof value === "string") return files[value] || value;
  if (Array.isArray(value)) return value.map((v) => substitute(v, files));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, substitute(v, files)]));
  }
  return value;
}

async function resolveConfig() {
  const hash = new URLSearchParams(location.hash.slice(1));
  const snapshot = hash.get("setup");
  const steps = [];
  let plugins = [];

  const gistId = query.get("gist");
  if (gistId) {
    statusEl.textContent = `Reading gist ${gistId}…`;
    const r = await fetch(`https://api.github.com/gists/${encodeURIComponent(gistId)}`);
    if (!r.ok) throw new Error(`gist ${gistId}: HTTP ${r.status}`);
    const files = Object.values((await r.json()).files);
    const rawUrls = Object.fromEntries(files.map((f) => [f.filename, f.raw_url]));
    for (const f of files) {
      if (f.filename.endsWith(".py")) {
        plugins.push({ url: f.raw_url, label: `gist ${gistId}: ${f.filename}`, source: f.truncated ? null : f.content });
      }
    }
    const setupFile = files.find((f) => f.filename === "setup.json");
    if (setupFile && !snapshot) {
      const text = setupFile.truncated ? await fetchText(setupFile.raw_url) : setupFile.content;
      steps.push(...substitute(JSON.parse(text), rawUrls));
    }
  }
  if (!snapshot) {
    for (const u of query.getAll("setup")) steps.push(...JSON.parse(await fetchText(u)));
  }
  if (snapshot) steps.push(...(await decodeSetup(snapshot)));   // a snapshot replaces the other setup sources

  for (const u of query.getAll("plugin")) plugins.push({ url: new URL(u, location.href).href, label: u });

  // Plugins are Python code. Code from another site needs the visitor's OK.
  const foreign = plugins.filter((p) => new URL(p.url).origin !== location.origin);
  if (foreign.length && !(await confirmPlugins(foreign))) {
    plugins = plugins.filter((p) => !foreign.includes(p));
  }
  for (const p of plugins) p.source = p.source ?? (await fetchText(p.url));

  return {
    plugins: plugins.map(({ url, source }) => ({ url, source })),
    steps,
    south: snapshot ? null : query.get("south"),
    csv: snapshot ? null : query.get("csv"),
    rate: query.get("rate"),
  };
}

function confirmPlugins(list) {
  const box = $("trust");
  $("trust-list").replaceChildren(...list.map((p) => {
    const li = document.createElement("li");
    li.textContent = p.label;
    return li;
  }));
  box.hidden = false;
  statusEl.textContent = "Waiting for your choice…";
  return new Promise((resolve) => {
    $("trust-run").onclick = () => { box.hidden = true; resolve(true); };
    $("trust-skip").onclick = () => { box.hidden = true; resolve(false); };
  });
}

// ---------------------------------------------------------- config -> URL
async function shareLink() {
  const id = nextId++;
  const json = await new Promise((resolve) => {
    exports.set(id, resolve);
    worker.postMessage({ type: "export", id });
  });
  const keep = new URLSearchParams();
  for (const k of ["gist", "plugin", "view"]) for (const v of query.getAll(k)) keep.append(k, v);
  const qs = keep.toString();
  return `${location.origin}${location.pathname}${qs ? "?" + qs : ""}#setup=${await encodeSetup(JSON.parse(json))}`;
}

$("share").onclick = async () => {
  const panel = $("share-panel");
  const input = $("share-url");
  input.value = "Building link…";
  panel.hidden = false;
  try {
    input.value = await shareLink();
    input.select();
    await navigator.clipboard.writeText(input.value);
    $("share-note").textContent = "Copied. The link recreates the south services, settings and filters you have now.";
  } catch (e) {
    $("share-note").textContent = String(e).includes("clipboard") ? "Copy the link above." : String(e);
  }
};
$("share-close").onclick = () => ($("share-panel").hidden = true);

// ------------------------------------------------------------------ start
async function start() {
  if (!("serviceWorker" in navigator)) {
    fail("This browser has no service worker support (or the page is not on https/localhost).");
    return;
  }
  // Requests from the service worker. Only answer the ones for our session.
  navigator.serviceWorker.addEventListener("message", (ev) => {
    const m = ev.data;
    if (!m || m.type !== "fledge-request" || m.session !== session) return;
    const id = nextId++;
    pending.set(id, ev.ports[0]);
    worker.postMessage({ type: "request", id, method: m.method, path: m.path, headers: m.headers, body: m.body },
      m.body ? [m.body] : []);
  });
  navigator.serviceWorker.startMessages();

  statusEl.textContent = "Registering service worker…";
  await navigator.serviceWorker.register("sw.js");
  await navigator.serviceWorker.ready;

  const config = await resolveConfig();
  worker.postMessage({ type: "init", params: config });
  await ready;

  // The GUI reads its API address from localStorage (same origin as this page).
  const api = new URL(`api/${session}/fledge/`, location.href);
  localStorage.setItem("SERVICE_URL", api.href);
  localStorage.setItem("CONNECTED_PROTOCOL", api.protocol.replace(":", ""));
  localStorage.setItem("CONNECTED_HOST", api.hostname);
  localStorage.setItem("CONNECTED_PORT", api.port || (api.protocol === "https:" ? "443" : "80"));

  const gui = $("gui");
  const view = query.get("view");
  gui.src = "gui/index.html" + (view ? "#/" + view.replace(/^#?\/?/, "") : "");
  gui.style.display = "block";
  $("loading").style.display = "none";
  $("share").disabled = false;
}

start().catch((e) => fail(String(e)));

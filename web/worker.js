// fledge-lite Web Worker (module worker): runs the Fledge core REST API in Pyodide.
import { loadPyodide } from "./pyodide/pyodide.mjs";

let ready = null;
let dispatch = null;
let pyodide = null;

function status(text) {
  self.postMessage({ type: "status", text });
}

async function boot(params) {
  status("Loading Python (Pyodide)…");
  pyodide = await loadPyodide({ indexURL: new URL("./pyodide/", import.meta.url).href });
  status("Loading aiohttp…");
  await pyodide.loadPackage(["aiohttp"], { messageCallback: () => {} });
  status("Unpacking Fledge…");
  const zip = await (await fetch("./fledge-bundle.zip")).arrayBuffer();
  pyodide.unpackArchive(zip, "zip", { extractDir: "/fledge" });
  status("Starting the Fledge core…");
  pyodide.globals.set("params_json", JSON.stringify(params));
  await pyodide.runPythonAsync(`
import sys, json, logging, warnings
warnings.simplefilter("ignore", SyntaxWarning)
sys.path[:0] = ["/fledge/lite", "/fledge/python"]
logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")
from fledge_lite import boot, web_entry
await boot.start("/fledge", "/fledge/scripts/plugins/storage/sqlite/init.sql")
`);
  dispatch = pyodide.globals.get("web_entry").dispatch_for_js;
  status("Applying the setup from the link…");
  const applied = await pyodide.globals.get("web_entry").apply_setup(JSON.stringify(params));
  return JSON.parse(applied);
}

self.onmessage = async (ev) => {
  const m = ev.data;
  if (m.type === "init") {
    ready = boot(m.params);
    try {
      const setupLog = await ready;
      self.postMessage({ type: "ready", setupLog });
    } catch (e) {
      self.postMessage({ type: "error", text: String(e) });
    }
    return;
  }
  if (m.type === "export") {
    await ready;
    const json = await pyodide.globals.get("web_entry").export_setup();
    self.postMessage({ type: "exported", id: m.id, json });
    return;
  }
  if (m.type === "request") {
    await ready;
    let result;
    try {
      const body = m.body ? new Uint8Array(m.body) : new Uint8Array();
      const out = await dispatch(m.method, m.path, JSON.stringify(m.headers || {}), body);
      const [st, hdrs, bytes] = out.toJs();
      out.destroy();
      result = { status: st, headers: JSON.parse(hdrs), body: bytes.buffer };
    } catch (e) {
      result = { status: 500, headers: { "Content-Type": "application/json" },
                 body: new TextEncoder().encode(JSON.stringify({ message: String(e) })).buffer };
    }
    self.postMessage({ type: "response", id: m.id, ...result }, [result.body]);
  }
};

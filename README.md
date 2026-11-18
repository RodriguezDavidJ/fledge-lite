# Fledge Lite

A proof of concept: the real [Fledge](https://github.com/fledge-iot/fledge) core REST API and the
unmodified Fledge GUI running entirely in a browser tab, configured by the URL. The idea comes from
Datasette Lite's service-worker architecture.

An unofficial experiment, not affiliated with LF Edge, the Fledge project or Dianomic. Fledge,
the Fledge GUI and Pyodide are used under their own licenses (Apache 2.0 and MPL 2.0).

```
index.html (shell.js) ── owns a session, starts a Web Worker, relays requests
   │
   ├─ <iframe> Fledge GUI (stock Angular build)
   │      fetch("api/<session>/fledge/south")
   │             │
   │        sw.js (service worker) intercepts api/<session>/… and posts it to the owning tab
   │             │
   └─ worker.js (Pyodide) ── the real fledge.services.core aiohttp app, called in-process
                               ├─ storage microservice emulated on SQLite (storage_emu.py)
                               ├─ scheduler starts "processes" → asyncio tasks (processes.py)
                               └─ south services + filter pipeline (services.py, Python plugins)
```

## Try it

**Hosted:** https://rodriguezdavidj.github.io/fledge-lite/?setup=samples/pump_demo.json

**Locally:**

```bash
./build.sh                                   # fetches pinned Fledge + GUI, builds the GUI, fetches Pyodide
python3 -m http.server -d dist 8000
# open http://localhost:8000/?setup=samples/pump_demo.json
```

Service workers need `https://` or `localhost`. Any static host works (GitHub Pages, S3, a file share
served by nginx on an engineering workstation).

### URL parameters

| Parameter | Effect |
|---|---|
| `?setup=<url>` | JSON list of Fledge REST calls to replay at startup, e.g. `[{"method":"POST","path":"/fledge/service","body":{…}}]`. `{"sleep": 0.5}` steps are allowed. See `samples/pump_demo.json`. Can be repeated. |
| `#setup=<encoded>` | The same list, deflated and base64url-encoded into the link itself. This is what **Share setup** produces. It replaces `?setup`, `?south`, `?csv` and a gist's `setup.json`. Plain JSON works too: `#setup=[{"method":…}]`. |
| `?south=simpump,sinusoid` | Start a south service for each plugin |
| `?csv=<url>` | Replay a CSV file through the `csvplayback` south plugin (the URL must allow CORS) |
| `?rate=<n>` | Readings per second for services started by `?south` / `?csv` |
| `?plugin=<url>` | Install a Fledge Python south or filter plugin from a `.py` file before setup runs. Can be repeated. See `samples/plugins/tank.py` for a commented example to copy. |
| `?gist=<id>` | Load a GitHub gist: every `.py` file is installed as a plugin, `setup.json` is replayed, and any value in it that names a gist file (e.g. `"url": {"value": "data.csv"}`) is replaced with that file's raw URL |
| `?view=south` | Open the GUI on a page (`south`, `asset`, `logs/audit`, `configuration`, …) |

The setup format is just the Fledge REST API, so the same JSON can configure a real Fledge instance
with `curl`, and **Share setup** is a quick way to write one: build a pipeline in the GUI, click it,
and the link (or the JSON inside it) recreates the south services, their changed settings, reading
rates and filter pipelines.

Examples:

- `?south=simpump&rate=2&view=asset`: one simulated pump, readings page open
- `?plugin=samples/plugins/tank.py&south=tank`: a plugin loaded from a URL
- `?gist=<id>`: a gist with `my_plugin.py`, `setup.json` and `data.csv` is a complete, shareable demo

### What a link can and can't do

Setup steps are calls to the Fledge REST API running in the visitor's own tab, so a setup link can
only configure that sandboxed instance. The `expression` filter accepts arithmetic and math functions
only (checked against a whitelist, not passed to `eval` as-is), so shared configs carry data, not code.

Plugins (`?plugin=`, `.py` files in a gist) are code. Plugins from another site run only after the
visitor clicks **Run them** on a prompt that lists their URLs. They run inside the tab with this
page's access, which on GitHub Pages includes browser storage shared by every site under the same
`<user>.github.io` origin.

## Deploying to GitHub Pages

`.github/workflows/pages.yml` builds the site on every push to `main` and publishes `dist/`. Once per
repository, set **Settings → Pages → Build and deployment → Source** to **GitHub Actions**. The first
build takes about 5 minutes (GUI build plus a 340 MB Pyodide download); later builds use the cache.

The Fledge and GUI commits are pinned in `build.sh` (`FLEDGE_REF`, `FLEDGE_GUI_REF`), because the
storage emulator and patches rely on Fledge internals. Bump them on purpose and rerun the tests.

## What is real and what is emulated

**Real (unmodified Fledge code):** the core REST API (`fledge.services.core`), configuration manager,
scheduler, service registry, asset tracker, audit log, filter and pipeline APIs, plugin discovery,
the statistics-history task, and the GUI.

**Replaced:**

| Fledge piece | In the browser |
|---|---|
| C++ storage service (HTTP + JSON query language → SQLite/Postgres) | `storage_emu.py`: the same REST API on in-memory SQLite, translating the subset of the JSON query dialect the core and GUI use |
| Processes started by the scheduler (`services/south_c`, `tasks/statistics`, …) | `processes.py`: fake process objects; south services and statistics run as asyncio tasks, the rest report success |
| C/C++ south and filter plugins | Python plugins with the standard Fledge Python plugin interface: `sinusoid`, `simpump` (pump with an injectable bearing fault), `csvplayback`, filters `scale` and `expression` |
| Sockets, syslog, mDNS, `/etc/os-release`, `subprocess` | Stubbed in `boot.py` |
| aiohttp's server | `request.py` builds `web.Request` objects and calls the app's router directly |

## Limits found

- **No device protocols.** Browsers can't open raw TCP/UDP sockets, so there's no Modbus TCP, OPC UA
  or similar. Data comes from simulators, CSV/HTTP sources, or (not built) WebSerial/WebUSB.
- **No north.** Sending to PI/OMF or another HTTP endpoint would need that endpoint to allow CORS.
- **State is per tab and in memory.** Reload and it is gone; the setup URL rebuilds it. **Share setup**
  captures configuration, not readings.
- **Only Python plugins.** The C++ plugin ecosystem would need compiling to WebAssembly.
- **Query dialect coverage is partial.** Pages that use JSON-query features not implemented in
  `storage_emu.py` will return errors. Checked in headless Chromium: Dashboard (statistics graph),
  South, Readings (graphs, summaries, buckets), Audit log, Configuration and Schedules render with
  data; North, Notifications, Syslog, Support and Settings load without API errors (mostly empty).
- **No login.** Authentication is forced to optional. Backups, notifications, the control dispatcher
  and north tasks are not implemented.

## Layout

```
web/            shell page, service worker, Pyodide worker
py/fledge_lite/ boot + storage emulator + in-browser services + plugins + setup import/export
samples/        pump_trip.csv, pump_demo.json, plugins/tank.py
tests/          smoke.py / scenario.py / export.py (CPython, no browser)
                browser.js / browser_long.js / browser_features.js (Playwright, against a local server)
build.sh        assembles dist/
.github/        GitHub Pages workflow
```

Tests: `python tests/scenario.py <fledge checkout>` runs the core in-process under CPython (needs
`aiohttp aiohttp-cors pyjwt async-timeout`). `node tests/browser.js <outdir>` drives headless Chromium
against `http://localhost:8000/`.

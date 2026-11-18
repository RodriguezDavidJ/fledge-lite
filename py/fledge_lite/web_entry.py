"""Glue between the Web Worker (JavaScript) and the in-process Fledge core."""

import asyncio
import json
import logging
import os
import re
import sys
import types
import urllib.parse

from fledge_lite import boot

log = logging.getLogger("fledge_lite.web")


async def dispatch_for_js(method, path, headers_json, body):
    """Called from worker.js. body arrives as a JsProxy Uint8Array."""
    raw = body.to_py() if hasattr(body, "to_py") else bytes(body or b"")
    status, headers, out = await boot.dispatch(method, path, json.loads(headers_json), bytes(raw))
    return status, json.dumps(headers), out


async def _call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    status, _, out = await boot.dispatch(method, path, {"Content-Type": "application/json"}, data)
    return status, out.decode(errors="replace")


# ------------------------------------------------------------------ plugins
def install_plugin(source, origin="<plugin>"):
    """Install a Fledge Python plugin from its source code.

    The plugin type and name come from its own plugin_info(), exactly as Fledge
    discovers them; the file is written where Fledge looks for Python plugins.
    """
    probe = types.ModuleType("fledge_lite_plugin_probe")
    exec(compile(source, origin, "exec"), probe.__dict__)
    info = probe.plugin_info()
    kind = info["type"]
    name = info["config"]["plugin"]["default"]
    if kind not in ("south", "filter"):
        raise ValueError("{}: only south and filter plugins are supported, got {!r}".format(origin, kind))
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("{}: invalid plugin name {!r}".format(origin, name))
    d = os.path.join(os.environ["FLEDGE_ROOT"], "python", "fledge", "plugins", kind, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "__init__.py"), "w").close()
    with open(os.path.join(d, name + ".py"), "w") as f:
        f.write(source)
    sys.modules.pop("fledge.plugins.{0}.{1}.{1}".format(kind, name), None)
    return {"plugin": name, "type": kind, "from": origin}


# -------------------------------------------------------------------- setup
async def apply_setup(params_json):
    """Configure the instance. params (resolved by shell.js from the URL):

    plugins  [{"url": ..., "source": ...}]  Python plugins to install first
    steps    [{"method", "path", "body"} | {"sleep": s}]  Fledge REST calls to replay
    south    "simpump,sinusoid"  start a south service per plugin
    csv      URL of a CSV to replay with csvplayback
    rate     readings per second for services started by south/csv
    """
    params = json.loads(params_json)
    results = []
    for p in params.get("plugins") or []:
        try:
            results.append(install_plugin(p["source"], p.get("url") or "<plugin>"))
        except Exception as e:
            log.warning("plugin %s failed to install: %r", p.get("url"), e)
            results.append({"plugin": p.get("url"), "error": repr(e)})

    steps = list(params.get("steps") or [])
    rate = params.get("rate")
    started = []
    for plugin in [p for p in (params.get("south") or "").split(",") if p]:
        steps.append({"method": "POST", "path": "/fledge/service",
                      "body": {"name": plugin, "type": "south", "plugin": plugin, "enabled": True}})
        started.append(plugin)
    if params.get("csv"):
        steps.append({"method": "POST", "path": "/fledge/service",
                      "body": {"name": "csv", "type": "south", "plugin": "csvplayback", "enabled": True,
                               "config": {"url": {"value": params["csv"]}}}})
        started.append("csv")
    if rate and started:
        steps.append({"sleep": 0.5})
        for n in started:
            steps.append({"method": "PUT", "path": "/fledge/category/{}Advanced".format(urllib.parse.quote(n)),
                          "body": {"readingsPerSec": str(rate)}})

    for s in steps:
        if "sleep" in s:
            await asyncio.sleep(min(float(s["sleep"]), 10))
            continue
        status, out = await _call(s.get("method", "GET"), s["path"], s.get("body"))
        results.append({"method": s.get("method", "GET"), "path": s["path"], "status": status, "response": out[:300]})
        if status >= 400:
            log.warning("setup step %s %s -> %s %s", s.get("method"), s["path"], status, out[:300])
    return json.dumps(results)


# ------------------------------------------------------------------- export
def _changed(items, skip=()):
    """Items whose value differs from the plugin default."""
    return {k: v["value"] for k, v in (items or {}).items()
            if k not in skip and isinstance(v, dict) and "value" in v and v.get("value") != v.get("default")}


async def export_setup():
    """Snapshot south services, their settings and filter pipelines as replayable REST calls."""
    from fledge.common.configuration_manager import ConfigurationManager
    from fledge.services.core import server

    S = server.Server
    cfg = ConfigurationManager(S._storage_client_async)
    services, advanced, filters = [], [], []
    posted_filters = set()
    for sch in await S.scheduler.get_schedules():
        if sch.process_name != "south_c":
            continue
        name = sch.name
        cat = await cfg.get_category_all_items(name)
        if not cat:
            continue
        body = {"name": name, "type": "south", "plugin": cat["plugin"]["value"], "enabled": bool(sch.enabled)}
        config = _changed(cat, skip=("plugin", "filter"))
        if config:
            body["config"] = {k: {"value": v} for k, v in config.items()}
        services.append({"method": "POST", "path": "/fledge/service", "body": body})

        adv = _changed(await cfg.get_category_all_items(name + "Advanced"))
        if adv:
            advanced.append({"method": "PUT", "path": "/fledge/category/{}Advanced".format(urllib.parse.quote(name)),
                             "body": adv})

        try:
            pipeline = json.loads(cat.get("filter", {}).get("value") or "{}").get("pipeline", [])
        except ValueError:
            pipeline = []
        names = []
        for f in pipeline:
            if not isinstance(f, str):
                continue
            fc = await cfg.get_category_all_items("{}_{}".format(name, f)) or await cfg.get_category_all_items(f)
            if not fc:
                continue
            if f not in posted_filters:
                posted_filters.add(f)
                filters.append({"method": "POST", "path": "/fledge/filter",
                                "body": {"name": f, "plugin": fc["plugin"]["value"],
                                         "filter_config": _changed(fc, skip=("plugin",))}})
            names.append(f)
        if names:
            filters.append({"method": "PUT",
                            "path": "/fledge/filter/{}/pipeline?allow_duplicates=true&append_filter=true".format(
                                urllib.parse.quote(name)),
                            "body": {"pipeline": names}})
    steps = services + ([{"sleep": 0.5}] + advanced if advanced else []) + filters
    return json.dumps(steps)

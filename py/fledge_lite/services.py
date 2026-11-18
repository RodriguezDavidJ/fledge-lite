"""In-browser implementations of the processes the Fledge scheduler starts.

  services/south_c  -> south_service(): polls a Python south plugin, runs the
                       filter pipeline, writes readings + statistics + asset tracker
  tasks/statistics  -> statistics_task(): Fledge's own StatisticsHistory.run()

Everything else the scheduler starts (purge, etc.) is reported as having run.
"""

import asyncio
import copy
import importlib
import itertools
import json
import logging

from fledge_lite import processes

log = logging.getLogger("fledge_lite.services")
_ports = itertools.count(40000)
running = {}  # service name -> FakeProcess


def _server():
    from fledge.services.core import server
    return server.Server


def _load_plugin(kind, name):
    return importlib.import_module("fledge.plugins.{0}.{1}.{1}".format(kind, name))


async def _bump_stats(storage, counts):
    """Increment statistics rows, creating per-asset rows on first use (as south_c does)."""
    from fledge.common.storage_client.payload_builder import PayloadBuilder
    for key, n in counts.items():
        key = key.upper()
        upd = {"expressions": [{"column": "value", "operator": "+", "value": n}],
               "where": {"column": "key", "condition": "=", "value": key}}
        res = await storage.update_tbl("statistics", json.dumps(upd))
        if res.get("rows_affected", 0) == 0:
            await storage.insert_into_tbl("statistics", json.dumps(
                {"key": key, "description": "Readings received from asset " + key, "value": n, "previous_value": 0}))


class FilterPipeline:
    """Fledge Python filter interface: plugin_init(config, ingest_ref, callback); plugin_ingest(handle, data)."""

    def __init__(self):
        self.stages = []  # (name, module, handle, config_version)
        self._out = None

    async def load(self, cfg_mgr, service_name):
        item = await cfg_mgr.get_category_item(service_name, "filter")
        names = []
        if item:
            try:
                names = json.loads(item["value"]).get("pipeline", [])
            except (ValueError, KeyError):
                names = []
        names = [n for n in names if isinstance(n, str)]  # (branches not supported)
        fingerprint = []
        configs = []
        for n in names:
            # Fledge keeps a per-service copy of each filter's config: "<service>_<filter>"
            cfg = (await cfg_mgr.get_category_all_items("{}_{}".format(service_name, n))
                   or await cfg_mgr.get_category_all_items(n) or {})
            configs.append(cfg)
            fingerprint.append(json.dumps(cfg, sort_keys=True))
        if fingerprint == [s[3] for s in self.stages] and len(names) == len(self.stages):
            return
        self.stages = []
        for n, cfg, fp in zip(names, configs, fingerprint):
            try:
                mod = _load_plugin("filter", cfg["plugin"]["value"])
                handle = mod.plugin_init(copy.deepcopy(cfg), n, self._collect)
                self.stages.append((n, mod, handle, fp))
            except Exception:
                log.exception("could not load filter %s", n)

    def _collect(self, ref, data):
        self._out = data

    def run(self, readings):
        for name, mod, handle, _ in self.stages:
            self._out = readings
            mod.plugin_ingest(handle, readings)
            readings = self._out or []
        return readings


async def south_service(proc, args):
    from fledge.common.configuration_manager import ConfigurationManager
    from fledge.services.core.service_registry.service_registry import ServiceRegistry
    from fledge.common.service_record import ServiceRecord

    name = processes.arg(args, "name")
    S = _server()
    storage = S._storage_client_async
    readings_client = S._readings_client_async
    cfg_mgr = ConfigurationManager(storage)

    adv = name + "Advanced"
    await cfg_mgr.create_category(adv, {
        "readingsPerSec": {"description": "Number of readings to generate per sec", "type": "integer",
                           "default": "1", "displayName": "Reading Rate", "order": "1"},
    }, "{} advanced config params".format(name), True, display_name="Advanced Configuration")
    await cfg_mgr.create_child_category(name, [adv])

    port = next(_ports)
    running[name] = proc
    svc_id = ServiceRegistry.register(name, "Southbound", "browser", port, port + 1, "http")
    cfg = await cfg_mgr.get_category_all_items(name)
    plugin_name = cfg["plugin"]["value"]
    plugin = _load_plugin("south", plugin_name)
    handle = plugin.plugin_init(copy.deepcopy(cfg))
    pipeline = FilterPipeline()
    tracked = set()
    last_cfg = json.dumps(cfg, sort_keys=True)
    log.info("south service %s started with plugin %s", name, plugin_name)
    try:
        tick = 0
        while True:
            adv_cfg = await cfg_mgr.get_category_all_items(adv)
            rate = max(0.01, float(adv_cfg["readingsPerSec"]["value"]))
            if tick % 5 == 0:
                cfg = await cfg_mgr.get_category_all_items(name)
                cfg_s = json.dumps(cfg, sort_keys=True)
                if cfg_s != last_cfg:
                    last_cfg = cfg_s
                    if hasattr(plugin, "plugin_reconfigure"):
                        handle = plugin.plugin_reconfigure(handle, copy.deepcopy(cfg))
                    else:
                        handle = plugin.plugin_init(copy.deepcopy(cfg))
                await pipeline.load(cfg_mgr, name)
            tick += 1

            data = plugin.plugin_poll(handle)
            if data:
                batch = data if isinstance(data, list) else [data]
                batch = pipeline.run(batch)
                if batch:
                    await readings_client.append(json.dumps({"readings": [
                        {"asset_code": r["asset"], "reading": r["readings"],
                         "user_ts": r.get("timestamp") or None} for r in batch]}))
                    counts = {"READINGS": len(batch)}
                    for r in batch:
                        counts[r["asset"]] = counts.get(r["asset"], 0) + 1
                        if r["asset"] not in tracked:
                            tracked.add(r["asset"])
                            try:
                                await S._asset_tracker.add_asset_record(
                                    asset=r["asset"], event="Ingest", service=name, plugin=plugin_name)
                            except Exception:
                                log.exception("asset tracker")
                    await _bump_stats(storage, counts)
            await asyncio.sleep(1.0 / rate)
    finally:
        running.pop(name, None)
        if hasattr(plugin, "plugin_shutdown"):
            try:
                plugin.plugin_shutdown(handle)
            except Exception:
                pass
        try:
            ServiceRegistry.unregister(svc_id)
        except Exception:
            pass
        log.info("south service %s stopped", name)


async def statistics_task(proc, args):
    from fledge.tasks.statistics.statistics_history import StatisticsHistory
    task = StatisticsHistory.__new__(StatisticsHistory)
    task._storage_async = _server()._storage_client_async
    task.is_dry_run = lambda: False
    await task.run()


def install():
    processes.handlers["services/south_c"] = south_service
    processes.handlers["tasks/statistics"] = statistics_task

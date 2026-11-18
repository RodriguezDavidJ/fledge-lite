"""Example south plugin to copy: a tank filled by a pump and drained by an outlet valve.

Load it with  ?plugin=<url of this file>  (a gist "raw" URL works), then add a south
service with the "tank" plugin in the GUI, or with ?south=tank.

A Fledge Python south plugin is a module with these functions:
  plugin_info()                 name, type and default configuration
  plugin_init(config)           returns a handle passed to the other calls
  plugin_poll(handle)           returns {"asset", "timestamp", "readings": {...}} (or a list of them)
  plugin_reconfigure(handle, config)  optional, called when settings change in the GUI
  plugin_shutdown(handle)       optional
"""
import random

_DEFAULT_CONFIG = {
    "plugin": {"description": "Tank level simulation (example plugin)", "type": "string",
               "default": "tank", "readonly": "true"},
    "assetName": {"description": "Asset name", "type": "string", "default": "tank01",
                  "displayName": "Asset name", "order": "1"},
    "inflow": {"description": "Pump flow in when running, m3/min", "type": "float", "default": "2.0",
               "displayName": "Inflow (m3/min)", "order": "2"},
    "valve": {"description": "Outlet valve opening, percent", "type": "float", "default": "40",
              "displayName": "Outlet valve (%)", "minimum": "0", "maximum": "100", "order": "3"},
    "high": {"description": "Pump stops above this level, m", "type": "float", "default": "4.5",
             "displayName": "Pump off level (m)", "order": "4"},
    "low": {"description": "Pump starts below this level, m", "type": "float", "default": "1.5",
            "displayName": "Pump on level (m)", "order": "5"},
}
AREA = 3.0  # m2


def plugin_info():
    return {"name": "tank", "version": "0.1.0", "mode": "poll", "type": "south",
            "interface": "1.0", "config": _DEFAULT_CONFIG}


def plugin_init(config):
    return {"config": config, "level": 2.0, "pump": True}


def plugin_poll(handle):
    c = handle["config"]
    level = handle["level"]
    if level >= float(c["high"]["value"]):
        handle["pump"] = False
    elif level <= float(c["low"]["value"]):
        handle["pump"] = True
    q_in = float(c["inflow"]["value"]) if handle["pump"] else 0.0
    q_out = 2.5 * (float(c["valve"]["value"]) / 100) * max(level, 0) ** 0.5   # orifice flow
    level = max(0.0, level + (q_in - q_out) / AREA / 6)                      # one poll ~ 10 s
    handle["level"] = level
    return {"asset": c["assetName"]["value"], "timestamp": None, "readings": {
        "level": round(level + random.gauss(0, 0.005), 3),
        "inflow": round(q_in, 3), "outflow": round(q_out, 3), "pump_running": int(handle["pump"])}}


def plugin_reconfigure(handle, new_config):
    handle["config"] = new_config
    return handle


def plugin_shutdown(handle):
    pass

"""Simulated centrifugal pump: flow, pressure, motor current, vibration. A fault can be injected."""
import math
import random
import time

_DEFAULT_CONFIG = {
    "plugin": {"description": "Simulated pump (in-browser Python)", "type": "string",
               "default": "simpump", "readonly": "true"},
    "assetName": {"description": "Asset name", "type": "string", "default": "pump01",
                  "displayName": "Asset name", "mandatory": "true", "order": "1"},
    "speed": {"description": "Pump speed in percent", "type": "float", "default": "80",
              "displayName": "Speed (%)", "minimum": "0", "maximum": "100", "order": "2"},
    "fault": {"description": "Inject a bearing fault (rising vibration, extra current)", "type": "boolean",
              "default": "false", "displayName": "Bearing fault", "order": "3"},
    "noise": {"description": "Measurement noise as a fraction of value", "type": "float", "default": "0.02",
              "displayName": "Noise", "order": "4"},
}


def plugin_info():
    return {"name": "simpump", "version": "0.1.0", "mode": "poll", "type": "south",
            "interface": "1.0", "config": _DEFAULT_CONFIG}


def plugin_init(config):
    return {"config": config, "t0": time.time(), "fault_since": None}


def _n(x, frac):
    return x * (1 + random.gauss(0, frac))


def plugin_poll(handle):
    c = handle["config"]
    speed = float(c["speed"]["value"]) / 100.0
    noise = float(c["noise"]["value"])
    fault = str(c["fault"]["value"]).lower() == "true"
    if fault and handle["fault_since"] is None:
        handle["fault_since"] = time.time()
    if not fault:
        handle["fault_since"] = None
    severity = 0.0 if handle["fault_since"] is None else min(1.0, (time.time() - handle["fault_since"]) / 120.0)
    t = time.time() - handle["t0"]
    flow = 120 * speed                        # m3/h, affinity law: Q ~ N
    pressure = 6.0 * speed ** 2               # bar,  H ~ N^2
    current = 40 * speed ** 3 + 5 + 8 * severity   # A, P ~ N^3
    vibration = 1.2 + 0.3 * math.sin(t / 7) + 6.0 * severity  # mm/s RMS
    return {"asset": c["assetName"]["value"], "timestamp": None, "readings": {
        "flow": round(_n(flow, noise), 3), "pressure": round(_n(pressure, noise), 3),
        "current": round(_n(current, noise), 3), "vibration": round(_n(vibration, noise), 3)}}


def plugin_reconfigure(handle, new_config):
    handle["config"] = new_config
    return handle


def plugin_shutdown(handle):
    pass

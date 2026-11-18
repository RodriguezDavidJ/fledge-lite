"""Python sinusoid south plugin for fledge-lite (same idea as Fledge's C sinusoid plugin)."""
import math

_DEFAULT_CONFIG = {
    "plugin": {"description": "Sinusoid waveform generator (in-browser Python)", "type": "string",
               "default": "sinusoid", "readonly": "true"},
    "assetName": {"description": "Name of asset", "type": "string", "default": "sinusoid",
                  "displayName": "Asset name", "mandatory": "true", "order": "1"},
    "steps": {"description": "Readings per full wave", "type": "integer", "default": "60",
              "displayName": "Steps per cycle", "order": "2"},
}


def plugin_info():
    return {"name": "sinusoid", "version": "0.1.0", "mode": "poll", "type": "south",
            "interface": "1.0", "config": _DEFAULT_CONFIG}


def plugin_init(config):
    return {"config": config, "step": 0}


def plugin_poll(handle):
    steps = max(2, int(handle["config"]["steps"]["value"]))
    v = math.sin(2 * math.pi * handle["step"] / steps)
    handle["step"] = (handle["step"] + 1) % steps
    return {"asset": handle["config"]["assetName"]["value"], "timestamp": None, "readings": {"sinusoid": round(v, 6)}}


def plugin_reconfigure(handle, new_config):
    handle["config"] = new_config
    return handle


def plugin_shutdown(handle):
    pass

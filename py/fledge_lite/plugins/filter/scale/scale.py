"""Scale filter: value * factor + offset for every numeric datapoint (like Fledge's C scale filter)."""

_DEFAULT_CONFIG = {
    "plugin": {"description": "Scale and offset numeric datapoints (in-browser Python)", "type": "string",
               "default": "scale", "readonly": "true"},
    "enable": {"description": "Enable filter", "type": "boolean", "default": "false", "displayName": "Enabled"},
    "factor": {"description": "Multiplier", "type": "float", "default": "1.0", "displayName": "Scale factor", "order": "1"},
    "offset": {"description": "Added after scaling", "type": "float", "default": "0.0", "displayName": "Offset", "order": "2"},
}


def plugin_info():
    return {"name": "scale", "version": "0.1.0", "mode": "none", "type": "filter",
            "interface": "1.0", "config": _DEFAULT_CONFIG}


def plugin_init(config, ingest_ref, callback):
    return {"config": config, "ref": ingest_ref, "cb": callback}


def plugin_ingest(handle, data):
    c = handle["config"]
    if str(c["enable"]["value"]).lower() != "true":
        handle["cb"](handle["ref"], data)
        return
    f, o = float(c["factor"]["value"]), float(c["offset"]["value"])
    out = []
    for r in data:
        rd = {k: (v * f + o if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
              for k, v in r["readings"].items()}
        out.append(dict(r, readings=rd))
    handle["cb"](handle["ref"], out)


def plugin_reconfigure(handle, new_config):
    handle["config"] = new_config
    return handle


def plugin_shutdown(handle):
    pass

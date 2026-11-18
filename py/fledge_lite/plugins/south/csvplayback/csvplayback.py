"""Replay rows of a CSV file fetched from a URL, one row per poll.

The URL must allow cross-origin requests (CORS) when running in the browser:
GitHub raw / gist URLs, S3 buckets with CORS, or files served next to fledge-lite.
"""
import csv
import io

_DEFAULT_CONFIG = {
    "plugin": {"description": "CSV playback from a URL (in-browser Python)", "type": "string",
               "default": "csvplayback", "readonly": "true"},
    "assetName": {"description": "Asset name", "type": "string", "default": "csv",
                  "displayName": "Asset name", "mandatory": "true", "order": "1"},
    "url": {"description": "CSV URL (needs CORS)", "type": "string", "default": "samples/pump_trip.csv",
            "displayName": "CSV URL", "mandatory": "true", "order": "2"},
    "skipColumns": {"description": "Comma separated columns to ignore (e.g. a timestamp column)", "type": "string",
                    "default": "timestamp", "displayName": "Columns to skip", "order": "3"},
    "loop": {"description": "Start again at the end of the file", "type": "boolean", "default": "true",
             "displayName": "Loop", "order": "4"},
}


def plugin_info():
    return {"name": "csvplayback", "version": "0.1.0", "mode": "poll", "type": "south",
            "interface": "1.0", "config": _DEFAULT_CONFIG}


def _fetch(url):
    try:
        from pyodide.http import open_url  # synchronous XHR; fine inside a Web Worker
        return open_url(url).read()
    except ImportError:
        import urllib.request
        import os
        if "://" not in url:
            base = os.environ.get("FLEDGE_LITE_BASE_DIR", ".")
            return open(os.path.join(base, url)).read()
        return urllib.request.urlopen(url).read().decode()


def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except (TypeError, ValueError):
        return v


def plugin_init(config):
    text = _fetch(config["url"]["value"])
    rows = list(csv.DictReader(io.StringIO(text)))
    skip = {s.strip() for s in config["skipColumns"]["value"].split(",") if s.strip()}
    return {"config": config, "rows": rows, "skip": skip, "i": 0}


def plugin_poll(handle):
    rows = handle["rows"]
    if handle["i"] >= len(rows):
        if str(handle["config"]["loop"]["value"]).lower() != "true" or not rows:
            return None
        handle["i"] = 0
    row = rows[handle["i"]]
    handle["i"] += 1
    readings = {k: _num(v) for k, v in row.items() if k not in handle["skip"] and v not in (None, "")}
    return {"asset": handle["config"]["assetName"]["value"], "timestamp": None, "readings": readings}


def plugin_reconfigure(handle, new_config):
    return plugin_init(new_config)


def plugin_shutdown(handle):
    pass

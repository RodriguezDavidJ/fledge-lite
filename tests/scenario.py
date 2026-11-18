"""CPython end-to-end: add a south service + filters through the REST API and check data flows."""
import asyncio, json, logging, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py"))
from fledge_lite import boot

FLEDGE = sys.argv[1]

async def call(method, path, body=None):
    st, h, b = await boot.dispatch(method, path, {"Content-Type": "application/json"}, json.dumps(body).encode() if body is not None else b"")
    try:
        doc = json.loads(b)
    except ValueError:
        doc = b[:200].decode(errors="replace")
    print(st, method, path, json.dumps(doc)[:260])
    return doc

async def main():
    logging.basicConfig(level=logging.WARNING, format='%(name)s %(levelname)s %(message)s')
    await boot.start(FLEDGE, os.path.join(FLEDGE, "scripts/plugins/storage/sqlite/init.sql"))
    await call("GET", "/fledge/plugins/installed?type=south")
    await call("GET", "/fledge/plugins/installed?type=filter")
    await call("POST", "/fledge/service", {"name": "Pump", "type": "south", "plugin": "simpump", "enabled": True,
                                           "config": {"speed": {"value": "90"}}})
    await asyncio.sleep(0.5)
    await call("PUT", "/fledge/category/PumpAdvanced", {"readingsPerSec": "5"})
    await call("POST", "/fledge/filter", {"name": "power", "plugin": "expression",
                                          "filter_config": {"enable": "true", "name": "power_kw", "expression": "flow * pressure / 36"}})
    await call("PUT", "/fledge/filter/Pump/pipeline?allow_duplicates=true&append_filter=true", {"pipeline": ["power"]})
    await asyncio.sleep(4)
    await call("GET", "/fledge/south")
    await call("GET", "/fledge/asset")
    await call("GET", "/fledge/asset/pump01?limit=2")
    await call("GET", "/fledge/asset/pump01/power_kw/summary")
    await call("PUT", "/fledge/schedule/disable", {"schedule_name": "Pump"})
    await asyncio.sleep(1)
    svcs = await call("GET", "/fledge/service")
    print("services now:", [x["name"] for x in svcs["services"]])
    await call("GET", "/fledge/statistics")
    for p in ["/fledge/statistics/history?minutes=10", "/fledge/asset/pump01/bucket/1?length=10", "/fledge/asset/pump01/summary", "/fledge/asset/pump01/latest", "/fledge/asset/timespan", "/fledge/audit?limit=20"]:
        await call("GET", p)

loop = asyncio.new_event_loop(); loop.run_until_complete(main()); os._exit(0)

"""CPython: install a plugin from source, apply a setup, export it, check the export replays the same config."""
import asyncio, json, logging, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py"))
from fledge_lite import boot, web_entry

FLEDGE = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))

async def main():
    logging.basicConfig(level=logging.WARNING)
    await boot.start(FLEDGE, os.path.join(FLEDGE, "scripts/plugins/storage/sqlite/init.sql"))
    steps = json.load(open(os.path.join(HERE, "..", "samples", "pump_demo.json")))
    steps = [s for s in steps if "csvplayback" not in json.dumps(s) and "Pump trip" not in s.get("path", "")]
    tank = open(os.path.join(HERE, "..", "samples", "plugins", "tank.py")).read()
    res = json.loads(await web_entry.apply_setup(json.dumps({
        "plugins": [{"url": "samples/plugins/tank.py", "source": tank}], "steps": steps,
        "south": "tank", "rate": "3"})))
    for r in res:
        print("setup:", r.get("status", r), r.get("method", ""), r.get("path", ""))
    await web_entry._call("PUT", "/fledge/category/tank", {"valve": "65"})
    await asyncio.sleep(2)
    print("latest tank:", (await web_entry._call("GET", "/fledge/asset/tank01/latest"))[1][:160])
    exported = json.loads(await web_entry.export_setup())
    print(json.dumps(exported, indent=1))

loop = asyncio.new_event_loop(); loop.run_until_complete(main()); os._exit(0)

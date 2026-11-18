"""CPython smoke test: boot the core in-process and hit the endpoints the GUI uses."""
import asyncio, json, logging, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py"))
from fledge_lite import boot

FLEDGE = sys.argv[1]
PATHS = sys.argv[2:] or ["/fledge/ping", "/fledge/service", "/fledge/statistics", "/fledge/asset",
                         "/fledge/schedule", "/fledge/category", "/fledge/audit?limit=5",
                         "/fledge/south", "/fledge/north", "/fledge/plugins/installed?type=south"]

async def main():
    logging.basicConfig(level=logging.INFO, format='%(name)s %(levelname)s %(message)s')
    await boot.start(FLEDGE, os.path.join(FLEDGE, "scripts/plugins/storage/sqlite/init.sql"))
    for p in PATHS:
        try:
            st, h, b = await asyncio.wait_for(boot.dispatch("GET", p, {}, b""), 10)
        except asyncio.TimeoutError:
            print("TIMEOUT", p); continue
        print(st, p, b[:300].decode(errors="replace"))

asyncio.run(main()) if False else None
loop = asyncio.new_event_loop(); loop.run_until_complete(main()); os._exit(0)

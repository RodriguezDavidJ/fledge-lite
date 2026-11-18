"""Stand-ins for the processes the Fledge scheduler launches.

The scheduler calls asyncio.create_subprocess_exec("services/south_c", "--name=X", ...).
In the browser we return a FakeProcess and, when we have one, run an in-browser
Python implementation of that service as an asyncio task instead.
"""

import asyncio
import itertools
import logging

log = logging.getLogger("fledge_lite.processes")

_pids = itertools.count(1000)
handlers = {}   # executable path -> async fn(fake_process, args) ; filled in by services.py
spawned = []


class FakeProcess:
    def __init__(self, args):
        self.args = args
        self.pid = next(_pids)
        self.returncode = None
        self._done = asyncio.get_event_loop().create_future()
        self.task = None

    def _finish(self, code=0):
        if self.returncode is None:
            self.returncode = code
            if not self._done.done():
                self._done.set_result(code)

    async def wait(self):
        return await self._done

    def terminate(self):
        if self.task:
            self.task.cancel()
        self._finish(-15)

    kill = terminate

    def send_signal(self, sig):
        self.terminate()


def arg(args, name, default=None):
    for a in args:
        if a.startswith("--{}=".format(name)):
            return a.split("=", 1)[1]
    return default


def spawn(args):
    p = FakeProcess(args)
    spawned.append(p)
    exe = args[0]
    handler = handlers.get(exe)
    if handler is None:
        log.info("no in-browser implementation for %s; pretending it ran", args)
        asyncio.get_event_loop().call_later(0.1, p._finish, 0)
        return p

    async def run():
        try:
            await handler(p, args)
            p._finish(0)
        except asyncio.CancelledError:
            p._finish(-15)
        except Exception:
            log.exception("in-browser process %s failed", args)
            p._finish(1)

    p.task = asyncio.ensure_future(run())
    return p

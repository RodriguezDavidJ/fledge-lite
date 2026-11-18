"""Boot the real Fledge core REST API in a single process with no sockets.

Works the same under CPython (for testing) and Pyodide (in a Web Worker).
"""

import asyncio
import json
import logging
import os
import subprocess
import sys

from fledge_lite import processes, storage_emu

log = logging.getLogger("fledge_lite")


# --------------------------------------------------------------- environment
def _patch_process_apis():
    """No processes in a browser: make subprocess calls return empty output."""

    def fake_run(args, *a, **k):
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def fake_check_output(args, *a, **k):
        return b""

    class FakePopen:
        def __init__(self, args, *a, **k):
            import io
            self.args, self.pid, self.returncode = args, 0, 0
            self.stdout, self.stderr = io.BytesIO(b""), io.BytesIO(b"")

        def communicate(self, *a, **k):
            return b"", b""

        def wait(self, *a, **k):
            return 0

        def poll(self):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    subprocess.run = fake_run
    subprocess.check_output = fake_check_output
    subprocess.Popen = FakePopen
    subprocess.call = lambda *a, **k: 0

    async def fake_exec(*args, **k):
        return processes.spawn(list(args))

    asyncio.create_subprocess_exec = fake_exec


def _stub_zeroconf():
    """Fledge announces itself over mDNS with zeroconf; there is no multicast in a browser."""
    import types
    zc = types.ModuleType("zeroconf")

    class _Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: None

    for n in ("ServiceInfo", "ServiceBrowser", "Zeroconf", "ServiceStateChange"):
        setattr(zc, n, _Dummy)
    sys.modules.setdefault("zeroconf", zc)


def _install_plugins(fledge_root):
    """Copy fledge-lite's Python plugins into the place Fledge looks for them."""
    import shutil
    src = os.path.join(os.path.dirname(__file__), "plugins")
    for kind in os.listdir(src):
        for name in os.listdir(os.path.join(src, kind)):
            dst = os.path.join(fledge_root, "python", "fledge", "plugins", kind, name)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(os.path.join(src, kind, name), dst)
        open(os.path.join(fledge_root, "python", "fledge", "plugins", kind, "__init__.py"), "a").close()


def _patch_syslog():
    """Fledge logs to syslog via /dev/log. Send those records to stderr (warnings and up) instead."""
    import logging.handlers

    class StderrSysLog(logging.StreamHandler):
        def __init__(self, *a, **k):
            super().__init__(sys.stderr)
            self.setLevel(logging.WARNING)

    logging.handlers.SysLogHandler = StderrSysLog


def _patch_service_management():
    """Core -> microservice management calls (ping, shutdown) for services running in this tab."""
    from fledge.services.common import utils
    from fledge_lite import services
    real_ping, real_shutdown = utils.ping_service, utils.shutdown_service

    async def ping_service(service, loop=None):
        if service._address == "browser":
            return service._name in services.running
        return await real_ping(service, loop)

    async def shutdown_service(service, loop=None):
        if service._address == "browser":
            proc = services.running.get(service._name)
            if proc:
                proc.terminate()
            return True
        return await real_shutdown(service, loop)

    utils.ping_service, utils.shutdown_service = ping_service, shutdown_service


def _fake_os_files():
    """Files Fledge reads from a Linux host, created in Pyodide's in-memory filesystem."""
    if sys.platform != "emscripten":
        return
    os.makedirs("/etc", exist_ok=True)
    if not os.path.exists("/etc/os-release"):
        with open("/etc/os-release", "w") as f:
            f.write('NAME="Browser"\nID=browser\nID_LIKE=debian\nVERSION_ID="1"\nPRETTY_NAME="Pyodide in a browser"\n')


def prepare(fledge_root):
    _fake_os_files()
    os.environ.setdefault("FLEDGE_ROOT", fledge_root)
    os.environ.setdefault("FLEDGE_DATA", os.path.join(fledge_root, "data"))
    os.makedirs(os.environ["FLEDGE_DATA"], exist_ok=True)
    os.makedirs(os.path.join(os.environ["FLEDGE_DATA"], "logs"), exist_ok=True)
    py = os.path.join(fledge_root, "python")
    if py not in sys.path:
        sys.path.insert(0, py)
    _patch_process_apis()
    _patch_syslog()
    _stub_zeroconf()
    _install_plugins(fledge_root)
    from fledge_lite import services
    services.install()
    _patch_service_management()


# ---------------------------------------------------------------------- core
class Core:
    app = None
    storage = None


async def start(fledge_root, init_sql_path):
    prepare(fledge_root)

    storage = storage_emu.SqliteStorage(init_sql_path)
    storage_emu.FakeSession.storage = storage
    Core.storage = storage

    # storage_client.py does `import aiohttp` and then `aiohttp.ClientSession()`.
    # Give that one module a proxy whose ClientSession is our in-process fake.
    import aiohttp
    from fledge.common.storage_client import storage_client

    class _AiohttpProxy:
        def __getattr__(self, name):
            if name == "ClientSession":
                return storage_emu.FakeSession
            return getattr(aiohttp, name)

    storage_client.aiohttp = _AiohttpProxy()

    from fledge.services.core import server
    from fledge.services.core.service_registry.service_registry import ServiceRegistry
    from fledge.common.service_record import ServiceRecord
    from fledge.services.core.interest_registry.interest_registry import InterestRegistry
    from fledge.common.configuration_manager import ConfigurationManager
    from fledge.common.audit_logger import AuditLogger

    # The service monitor pings each microservice over HTTP. In-browser services are
    # tasks in this same event loop, so skip the ping loop but keep its config setup.
    from fledge.services.core.service_registry import monitor as _monitor

    async def _idle_monitor_loop(self):
        while True:
            await asyncio.sleep(3600)

    _monitor.Monitor._monitor_loop = _idle_monitor_loop

    S = server.Server
    S._host = "localhost"
    S.core_management_port = 1
    S.rest_server_port = 8081
    S.running_in_safe_mode = False

    ServiceRegistry.register("Fledge Storage", "Storage", "localhost", 2, 3, "http")
    await S._get_storage_client()
    S._configuration_manager = ConfigurationManager(S._storage_client_async)
    S._interest_registry = InterestRegistry(S._configuration_manager)

    # Same order as Server._start_core, minus sockets/processes, with the logger
    # config added and parent categories created before the support bundle config.
    steps = ["setup_config_manager", "core_logger_setup", "core_south_service_resource_limit_setup",
             "_start_scheduler", "_start_service_monitor", "rest_api_config", "password_config",
             "firewall_config", "features_config", "service_config", "installation_config",
             "_config_parents", "support_bundle_config", "_start_asset_tracker", "_get_alerts"]
    for name in steps:
        fn = getattr(S, name, None)
        if fn is None:
            continue
        try:
            await asyncio.wait_for(fn(), timeout=10)
            log.info("startup step %s ok", name)
        except Exception as e:  # keep going: a partial core is still useful
            log.warning("startup step %s failed: %r", name, e)

    # fledge-lite runs without login; make the stored config say so too.
    try:
        await S._configuration_manager.set_category_item_value_entry("rest_api", "authentication", "optional")
    except Exception as e:
        log.warning("could not set authentication to optional: %r", e)

    S._register_core("localhost", 1, 8081)
    S.is_auth_required = False
    S.auth_method = "any"
    app = S._make_app(auth_required=False, auth_method="any")
    app.freeze()
    Core.app = app
    S.service_app = app

    S._audit = AuditLogger(S._storage_client_async)
    try:
        await S._audit.information("START", {"message": "fledge-lite started in the browser"})
    except Exception as e:
        log.warning("audit failed: %r", e)
    return app


# ------------------------------------------------------------------ dispatch
async def dispatch(method, path_qs, headers, body):
    """Run one HTTP request through the aiohttp app. Returns (status, headers, body_bytes)."""
    from aiohttp import web
    from fledge_lite.request import make_request

    req = make_request(method, path_qs, headers, Core.app, body)
    try:
        resp = await Core.app._handle(req)
    except web.HTTPException as e:
        resp = e
    except Exception as e:
        log.exception("unhandled error for %s %s", method, path_qs)
        return 500, {"Content-Type": "application/json"}, json.dumps({"message": repr(e)}).encode()
    body_out = resp.body
    if body_out is None:
        body_out = b""
    elif not isinstance(body_out, (bytes, bytearray)):
        body_out = getattr(body_out, "_value", b"")
        if isinstance(body_out, str):
            body_out = body_out.encode()
    hdrs = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "transfer-encoding")}
    if resp.content_type and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = resp.content_type
    return resp.status, hdrs, bytes(body_out)

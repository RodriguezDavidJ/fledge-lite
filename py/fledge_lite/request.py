"""Build an aiohttp web.Request without a socket.

Equivalent to aiohttp.test_utils.make_mocked_request, which Pyodide's aiohttp
package leaves out. Works with aiohttp 3.13 (Pyodide) and 3.14 (PyPI).
"""

import asyncio
import inspect
from unittest import mock

from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL
from aiohttp import web
from aiohttp.http import HttpVersion, RawRequestMessage
from aiohttp.streams import EMPTY_PAYLOAD
from aiohttp.web_urldispatcher import UrlMappingMatchInfo

_REQUEST_PARAMS = inspect.signature(web.Request.__init__).parameters


async def _noop(*a, **k):
    return None


class _Transport:
    """Enough of an asyncio transport for Fledge's firewall check."""

    def get_extra_info(self, name, default=None):
        if name in ("peername", "sockname"):
            return ("127.0.0.1", 0)
        return default

    def is_closing(self):
        return False


def make_request(method, path, headers, app, body=b""):
    headers = CIMultiDictProxy(CIMultiDict(headers or {}))
    raw = tuple((k.encode(), v.encode()) for k, v in headers.items())
    message = RawRequestMessage(method.upper(), path, HttpVersion(1, 1), headers, raw,
                                False, None, False, False, URL(path))
    transport = _Transport()
    protocol = mock.Mock()
    protocol.max_field_size = protocol.max_line_length = 8190
    protocol.max_headers = 128
    protocol.transport = transport
    type(protocol).peername = mock.PropertyMock(return_value=("127.0.0.1", 0))
    type(protocol).sockname = mock.PropertyMock(return_value=("127.0.0.1", 0))
    type(protocol).ssl_context = mock.PropertyMock(return_value=None)
    writer = mock.Mock()
    writer.write_headers = writer.write = writer.write_eof = writer.drain = _noop
    writer.transport = transport
    protocol.writer = writer
    kwargs = {"client_max_size": 64 * 1024 * 1024}
    if "client_max_fields" in _REQUEST_PARAMS:
        kwargs["client_max_fields"] = 1000
    req = web.Request(message, EMPTY_PAYLOAD, protocol, writer, mock.Mock(),
                      asyncio.get_event_loop(), **kwargs)
    match_info = UrlMappingMatchInfo({}, mock.Mock())
    match_info.add_app(app)
    req._match_info = match_info
    req._read_bytes = body or b""   # .read()/.text()/.json() return this
    return req

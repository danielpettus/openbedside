"""MLLP framing over TCP: <VT> message <FS><CR>. Async client and server."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"
log = logging.getLogger("openbedside.mllp")


def frame(msg: str) -> bytes:
    return VT + msg.encode("utf-8") + FS + CR


async def read_frame(reader: asyncio.StreamReader, timeout: float | None = None) -> str | None:
    """Read one framed message. Returns None on clean EOF."""
    async def _read() -> str | None:
        buf = bytearray()
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                return None
            buf.extend(chunk)
            end = buf.find(FS + CR)
            if end != -1:
                start = buf.find(VT)
                start = 0 if start == -1 else start + 1
                return buf[start:end].decode("utf-8", errors="replace")
    return await asyncio.wait_for(_read(), timeout) if timeout else await _read()


async def send(host: str, port: int, msg: str, timeout: float = 10.0) -> str:
    """Send one message and return the reply. Raises on timeout or connection error."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        writer.write(frame(msg))
        await writer.drain()
        reply = await read_frame(reader, timeout)
        if reply is None:
            raise ConnectionError("receiver closed the connection without replying")
        return reply
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


Handler = Callable[[str], Awaitable[str]]


async def serve(host: str, port: int, handler: Handler) -> asyncio.base_events.Server:
    """Listen for framed messages; the handler returns the reply (an ACK)."""
    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            while True:
                msg = await read_frame(reader)
                if msg is None:
                    break
                reply = await handler(msg)
                writer.write(frame(reply))
                await writer.drain()
        except Exception as e:                     # one bad client never stops the listener
            log.warning("MLLP client %s: %s", peer, e)
        finally:
            writer.close()
    return await asyncio.start_server(on_client, host, port)

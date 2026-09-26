"""A ready-made base for the most common kind of device connection: open a connection,
read messages one at a time, turn each into a snapshot, and reconnect when the
connection drops.

Most vendor integrations only need to fill in four methods:

    async def connect(self)          open the connection (TCP, serial, vendor SDK...)
    async def read_message(self)     return the next raw message, or None if closed
    def normalize(self, raw)         turn one raw message into a model.Device (or None to skip)
    async def close(self)            tidy up (optional)

Reconnection with backoff, logging and publishing are handled here.

What this class deliberately does NOT do: resend the last snapshot when the device
goes quiet. A gateway that repeats old data hides a lost connection. When the device
stops talking, the engine notices the silence and reports communication lost.
"""
from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any, Optional

from ..model import Device
from .base import DeviceAdapter

log = logging.getLogger("openbedside.adapter")


class StreamAdapter(DeviceAdapter):
    reconnect_min = 1.0
    reconnect_max = 30.0

    def __init__(self, config: dict, publish):
        super().__init__(config, publish)
        self.connected = False
        self.messages = 0
        self.skipped = 0
        self.last_error = ""
        self._known: set[str] = set()

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def read_message(self) -> Optional[Any]: ...

    @abc.abstractmethod
    def normalize(self, raw: Any) -> Optional[Device]: ...

    async def close(self) -> None:
        pass

    def owns(self, device_id: str) -> bool:
        return device_id in self._known

    async def run(self) -> None:
        delay = self.reconnect_min
        while True:
            try:
                await self.connect()
                self.connected = True
                delay = self.reconnect_min
                log.info("%s: connected", self.name)
                while True:
                    raw = await self.read_message()
                    if raw is None:
                        raise ConnectionError("connection closed by device")
                    self.messages += 1
                    try:
                        dev = self.normalize(raw)
                    except Exception as e:           # one bad message never kills the adapter
                        self.skipped += 1
                        log.warning("%s: could not normalize message: %s", self.name, e)
                        continue
                    if dev is None:
                        self.skipped += 1
                        continue
                    self._known.add(dev.device_id)
                    for m in dev.modules:
                        self._known.add(m.module_id)
                    self.publish(dev)
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as e:
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("%s: %s; reconnecting in %.0fs", self.name, self.last_error, delay)
                try:
                    await self.close()
                except Exception:
                    pass
                await asyncio.sleep(delay)
                delay = min(self.reconnect_max, delay * 2)

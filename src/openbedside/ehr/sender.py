"""Drains the outbox to the EHR's interface engine over MLLP.

A message counts as delivered only on a positive application ACK (AA or CA) whose
MSA-2 matches the control id we sent. Anything else is retried with backoff, except
an explicit reject (AR or CR), which is set aside for a human to look at.
"""
from __future__ import annotations

import asyncio
import logging

from ..hl7 import mllp
from ..hl7.ack import POSITIVE, REJECTED, parse_ack
from ..store.outbox import Outbox

log = logging.getLogger("openbedside.sender")


class Sender:
    def __init__(self, outbox: Outbox, destination: str, host: str, port: int, timeout: float = 10.0):
        self.outbox, self.destination, self.host, self.port, self.timeout = outbox, destination, host, port, timeout
        self.connected = False
        self.last_error = ""

    async def once(self) -> bool:
        """Try to deliver the head of the queue. Returns True if something was sent."""
        item = self.outbox.head(self.destination)
        if item is None:
            return False
        try:
            reply = await mllp.send(self.host, self.port, item.message, self.timeout)
            code, acked_id, text = parse_ack(reply)
        except Exception as e:
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"
            delay = self.outbox.mark_retry(item.id, item.attempts, self.last_error)
            log.warning("send failed (%s); retry in %.0fs", self.last_error, delay)
            return False
        self.connected = True
        if code in POSITIVE and acked_id == item.control_id:
            self.outbox.mark_sent(item.id, code)
            return True
        if code in REJECTED:
            self.outbox.mark_rejected(item.id, f"{code} {text}")
            log.error("message %s rejected by receiver: %s %s", item.control_id, code, text)
            return True
        detail = f"{code} {text}" if acked_id == item.control_id else f"ACK for {acked_id!r}, expected {item.control_id!r}"
        delay = self.outbox.mark_retry(item.id, item.attempts, detail)
        log.warning("not acknowledged (%s); retry in %.0fs", detail, delay)
        return False

    async def run(self, idle_sleep: float = 0.5) -> None:
        while True:
            sent = await self.once()
            if not sent:
                await asyncio.sleep(idle_sleep)

"""The device adapter contract. This is the only place a vendor protocol lives.

An adapter:
  * connects to devices however the vendor requires (serial, TCP, vendor SDK, a
    manufacturer's own gateway, a file drop);
  * normalizes what it hears into model.Device snapshots;
  * calls `publish(device)` every time something changes, and at least every
    `heartbeat` seconds while a device is online;
  * never invents values. Unknown stays None, and a number never travels without
    its unit.

Programming a device (auto-programming) is OPTIONAL and, in this release, only the
simulator implements it. See docs/DECISIONS.md, "FDA line".
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable, Optional

from ..model import Device

Publish = Callable[[Device], None]


class NotSupported(Exception):
    """Raised when an adapter is asked to do something it does not do."""


@dataclass
class ProgramRequest:
    """A normalized auto-programming request, parsed from an inbound PCD-03."""
    order_id: str
    device_id: str
    module_id: Optional[str]
    patient_id: Optional[str]
    drug_name: Optional[str]
    rate_ml_h: Optional[float]
    vtbi_ml: Optional[float]
    concentration: Optional[str] = None


@dataclass
class ProgramResult:
    accepted: bool
    reason: str = ""


class DeviceAdapter(abc.ABC):
    #: human readable name, shown in the UI
    name: str = "adapter"
    #: True only for adapters that are allowed to receive programming requests
    supports_programming: bool = False

    def __init__(self, config: dict, publish: Publish):
        self.config = config
        self.publish = publish

    @abc.abstractmethod
    async def run(self) -> None:
        """Connect and publish until cancelled."""

    async def program(self, request: ProgramRequest) -> ProgramResult:
        raise NotSupported(f"{self.name} does not accept programming requests")

    def owns(self, device_id: str) -> bool:
        """True if this adapter manages the device (used to route programming requests)."""
        return False

"""`openbedside new-adapter NAME` writes a starter folder for a manufacturer:

    NAME-adapter/
        NAME_adapter.py        the adapter, with the four methods to fill in
        NAME.toml              a config that runs it
        test_NAME_adapter.py   tests to grow as the mapping grows
        MAPPING.md             the mapping worksheet, to fill in first
"""
from __future__ import annotations

import os
import re
from importlib import resources

ADAPTER = '''"""OpenBedside adapter for {Title} devices.

Fill in the four marked sections. Everything else (reconnecting, publishing,
events, store-and-forward, HL7, the status page) is done by OpenBedside.
Work through MAPPING.md first: the mapping is the real work, the code is short.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from openbedside.adapters.stream import StreamAdapter
from openbedside.model import (Channel, Device, InfusionSource, Module, PumpStatus,
                               Quantity, SourceRole, utcnow)

# 1. YOUR VOCABULARY -> OURS. One table for states, one for units. Nothing else
#    in this file should know the device's own words.
STATE_MAP = {{
    "RUNNING": PumpStatus.INFUSING,
    "KVO": PumpStatus.KVO,
    "PAUSED": PumpStatus.PAUSED,
    "STOPPED": PumpStatus.IDLE,
    "COMPLETE": PumpStatus.COMPLETE,
    "ALARM": PumpStatus.ALARM,
}}
UNIT_MAP = {{            # device unit string -> UCUM
    "mL/hr": "mL/h",
    "mL": "mL",
    "mg/mL": "mg/mL",
    "units/mL": "[iU]/mL",
    "units/hr": "[iU]/h",
    "mcg/kg/min": "ug/kg/min",
    "kg": "kg",
}}


def q(value: Any, unit: str) -> Optional[Quantity]:
    if value is None:
        return None
    if unit not in UNIT_MAP:
        raise ValueError(f"unmapped unit {{unit!r}}: add it to UNIT_MAP")
    return Quantity(float(value), UNIT_MAP[unit])


class {Class}Adapter(StreamAdapter):
    name = "{name}"

    # 2. OPEN THE CONNECTION. TCP shown; for serial use pyserial-asyncio, for a
    #    vendor SDK call it here. Settings come from the [[adapters]] block.
    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(
            self.config.get("host", "127.0.0.1"), int(self.config.get("port", 7001)))

    # 3. READ ONE MESSAGE. Return None when the device closes the connection.
    #    Shown: one JSON object per line. Replace with your framing.
    async def read_message(self) -> Optional[Any]:
        line = await asyncio.wait_for(self.reader.readline(), 15)
        return json.loads(line) if line else None

    async def close(self) -> None:
        w = getattr(self, "writer", None)
        if w:
            w.close()

    # 4. TRANSLATE ONE MESSAGE INTO ONE SNAPSHOT. Return None to skip a message
    #    that is not a status report. Unknown values stay None; never guess.
    def normalize(self, raw: Any) -> Optional[Device]:
        modules = []
        for ch in raw.get("channels", []):
            status = STATE_MAP.get(ch.get("state"), PumpStatus.UNKNOWN)
            primary = InfusionSource(role=SourceRole.PRIMARY, drug_name=ch.get("drug"),
                                     rate=q(ch.get("rate"), "mL/hr"), vtbi=q(ch.get("vtbi"), "mL"),
                                     volume_delivered=q(ch.get("delivered"), "mL"))
            actual = primary.rate if status == PumpStatus.INFUSING else None
            module_id = f"{{raw['serial']}}-{{ch['id']}}"
            modules.append(Module(module_id, [Channel(f"{{module_id}}-1", status, actual, [primary])]))
        return Device(device_id=raw["serial"], vendor="{Title}", model=raw.get("model", "unknown"),
                      modules=modules, observed_at=utcnow())
'''

CONFIG = '''# Runs the {Title} adapter. From this folder:
#   openbedside check -c {name}.toml --seconds 60     (checks your mapping)
#   openbedside ehr-sim                               (in another tab)
#   openbedside run -c {name}.toml                    (the gateway, status page on :8080)

[gateway]
adapter_paths = ["."]          # lets OpenBedside find {name}_adapter.py in this folder
database = "{name}-test.db"

[ehr]
host = "127.0.0.1"
port = 6661

[orders]
enabled = false                # auto-programming is simulator-only; not used for real devices

[[adapters]]
type = "{name}_adapter:{Class}Adapter"
host = "127.0.0.1"             # your device, or its test harness
port = 7001
'''

TEST = '''"""Tests for the {Title} adapter. Run with:  pytest
Add a sample of every message type your device sends, and assert on the result."""
from openbedside.validate import check_device, ERROR
from openbedside.model import PumpStatus
from {name}_adapter import {Class}Adapter

SAMPLE = {{"serial": "DEV-0001", "model": "X1", "channels": [
    {{"id": "A", "state": "RUNNING", "drug": "heparin", "rate": 12, "vtbi": 250, "delivered": 10}}]}}


def adapter():
    return {Class}Adapter({{}}, lambda d: None)


def test_sample_normalizes_cleanly():
    dev = adapter().normalize(SAMPLE)
    errors = [f for f in check_device(dev) if f.level == ERROR]
    assert not errors, "\\n".join(map(str, errors))


def test_state_mapping():
    dev = adapter().normalize(SAMPLE)
    assert dev.modules[0].channels[0].status == PumpStatus.INFUSING
'''


def make(name: str, directory: str | None = None) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "device"
    klass = "".join(p.capitalize() for p in slug.split("_"))
    title = name.strip()
    target = os.path.abspath(directory or f"{slug}-adapter")
    if os.path.exists(target) and os.listdir(target):
        raise FileExistsError(f"{target} exists and is not empty")
    os.makedirs(target, exist_ok=True)
    fmt = dict(name=slug, Class=klass, Title=title)
    files = {f"{slug}_adapter.py": ADAPTER.format(**fmt), f"{slug}.toml": CONFIG.format(**fmt),
             f"test_{slug}_adapter.py": TEST.format(**fmt),
             "MAPPING.md": resources.files("openbedside").joinpath("mapping_worksheet.md").read_text()}
    for fname, text in files.items():
        with open(os.path.join(target, fname), "w", encoding="utf-8") as f:
            f.write(text)
    return target

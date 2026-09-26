"""A complete worked example of a vendor adapter, for a pump that does not exist.

"ACME" is fictional. Its protocol is invented for this example so that a real
manufacturer can see every step of an integration without anyone's proprietary
protocol appearing here:

  * the device listens on TCP and, once connected, sends one JSON object per line,
    every two seconds;
  * it uses its own words for states (RUN, HOLD, KVO, DONE, ALRM, STOP) and its own
    unit strings ("mL/hr", "units/mL", "mcg/kg/min");
  * a two-channel pump reports channels "L" and "R"; a piggyback appears as "sec".

Real devices differ in transport (serial, TCP, UDP, vendor SDK, a vendor server's
REST API) and in vocabulary. They carry the same facts. The adapter's whole job is
the translation below: transport in connect/read_message, vocabulary in normalize.

Run the fake device and the gateway against it:
    openbedside fake-pump
    openbedside run -c config/acme-example.toml
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any, Optional

from ..adapters.stream import StreamAdapter
from ..model import (Channel, Device, InfusionSource, Module, PumpStatus, Quantity,
                     SourceRole, utcnow)

# ---- 1. The vendor's vocabulary, mapped once, in one place --------------------
STATE_MAP = {
    "RUN": PumpStatus.INFUSING,
    "KVO": PumpStatus.KVO,          # still infusing, at the keep-vein-open rate
    "HOLD": PumpStatus.PAUSED,
    "STOP": PumpStatus.IDLE,
    "DONE": PumpStatus.COMPLETE,
    "ALRM": PumpStatus.ALARM,
}
UNIT_MAP = {                        # vendor string -> UCUM
    "mL/hr": "mL/h",
    "mL": "mL",
    "units/mL": "[iU]/mL",
    "units/hr": "[iU]/h",
    "mg/mL": "mg/mL",
    "mcg/kg/min": "ug/kg/min",
    "kg": "kg",
}


def q(value: Any, vendor_unit: str) -> Optional[Quantity]:
    """Vendor number + vendor unit -> Quantity. Unknown unit -> None, never a guess."""
    if value is None:
        return None
    ucum = UNIT_MAP.get(vendor_unit)
    if ucum is None:
        raise ValueError(f"unmapped ACME unit {vendor_unit!r}")
    return Quantity(float(value), ucum)


def pair(p: Any) -> Optional[Quantity]:
    return q(p[0], p[1]) if p else None


class AcmeJsonAdapter(StreamAdapter):
    name = "acme-json (example)"

    # ---- 2. Transport -----------------------------------------------------------
    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.config.get("host", "127.0.0.1"), int(self.config.get("port", 7001))), 10)

    async def read_message(self) -> Optional[dict]:
        line = await asyncio.wait_for(self.reader.readline(), float(self.config.get("read_timeout", 15)))
        if not line:
            return None
        return json.loads(line)

    async def close(self) -> None:
        w = getattr(self, "writer", None)
        if w:
            w.close()

    # ---- 3. Vocabulary: one raw message -> one normalized snapshot ---------------
    def normalize(self, raw: dict) -> Optional[Device]:
        if raw.get("type") != "status":
            return None                               # ignore anything that is not a status report
        serial = raw["serial"]
        modules = []
        for ch in raw.get("channels", []):
            status = STATE_MAP.get(ch.get("state"), PumpStatus.UNKNOWN)
            sources = [InfusionSource(
                role=SourceRole.PRIMARY, drug_name=ch.get("drug"), concentration=pair(ch.get("conc")),
                rate=q(ch.get("rate"), "mL/hr"), dose_rate=pair(ch.get("dose")),
                vtbi=q(ch.get("vtbi"), "mL"),
                vtbi_remaining=q(round(max(0.0, ch["vtbi"] - ch["vi"]), 3), "mL") if ch.get("vtbi") is not None and ch.get("vi") is not None else None,
                volume_delivered=q(ch.get("vi"), "mL"))]
            sec = ch.get("sec")
            if sec:
                sources.append(InfusionSource(
                    role=SourceRole.SECONDARY, drug_name=sec.get("drug"), concentration=pair(sec.get("conc")),
                    rate=q(sec.get("rate"), "mL/hr"), vtbi=q(sec.get("vtbi"), "mL"),
                    vtbi_remaining=q(round(max(0.0, sec["vtbi"] - sec["vi"]), 3), "mL"),
                    volume_delivered=q(sec.get("vi"), "mL")))
            if status == PumpStatus.KVO:
                actual = q(ch.get("kvo_rate"), "mL/hr")
            elif status == PumpStatus.INFUSING:
                actual = q((sec or ch).get("rate"), "mL/hr")   # the secondary runs while the primary waits
            else:
                actual = None
            module_id = f"{serial}-{ch['ch']}"
            modules.append(Module(module_id, [Channel(f"{module_id}-1", status, actual, sources)]))
        device_clock = datetime.fromisoformat(raw["time"].replace("Z", "+00:00")) if raw.get("time") else None
        return Device(device_id=serial, vendor="ACME (fictional)", model=raw.get("model", "ACME-2"),
                      modules=modules, patient_weight=q(raw.get("weight_kg"), "kg"),
                      observed_at=utcnow(), device_clock=device_clock)


# ---- The fake ACME device, so the example runs with no hardware ----------------------
async def fake_pump(host: str = "127.0.0.1", port: int = 7001, serial: str = "ACME-7731",
                    interval: float = 2.0, speed: float = 60.0) -> None:
    """Serve the invented ACME protocol. Synthetic values only."""
    state = {
        "L": {"state": "RUN", "drug": "heparin", "conc": [100, "units/mL"], "rate": 12.0,
              "vtbi": 250.0, "vi": 0.0, "dose_unit": "units/hr"},
        "R": {"state": "RUN", "drug": "sodium chloride 0.9%", "conc": None, "rate": 75.0,
              "vtbi": 500.0, "vi": 0.0,
              "sec": {"drug": "cefazolin", "conc": [20, "mg/mL"], "rate": 100.0, "vtbi": 50.0, "vi": 0.0}},
    }

    def tick(dt_h: float) -> None:
        for ch in state.values():
            if ch["state"] != "RUN":
                continue
            src = ch.get("sec") or ch
            src["vi"] = min(src["vtbi"], src["vi"] + src["rate"] * dt_h)
            if src["vi"] >= src["vtbi"]:
                if src is ch.get("sec"):
                    ch["sec"] = None
                else:
                    ch["state"] = "KVO"

    def message() -> str:
        chans = []
        for name, ch in state.items():
            item = {"ch": name, "state": ch["state"], "drug": ch["drug"], "conc": ch["conc"],
                    "rate": ch["rate"], "vtbi": ch["vtbi"], "vi": round(ch["vi"], 3), "kvo_rate": 1.0}
            if ch.get("dose_unit") and ch["conc"]:
                item["dose"] = [round(ch["rate"] * ch["conc"][0], 3), ch["dose_unit"]]
            if ch.get("sec"):
                s = ch["sec"]
                item["sec"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()}
            chans.append(item)
        return json.dumps({"type": "status", "serial": serial, "model": "ACME-2",
                           "time": datetime.now(timezone.utc).isoformat(), "channels": chans}) + "\n"

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                writer.write(message().encode())
                await writer.drain()
                await asyncio.sleep(interval)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def clock() -> None:
        while True:
            await asyncio.sleep(interval)
            tick(interval * speed / 3600.0)
            if random.random() < 0.02 and state["L"]["state"] == "RUN":
                state["L"]["rate"] = 14.0 if state["L"]["rate"] == 12.0 else 12.0   # an occasional titration

    srv = await asyncio.start_server(client, host, port)
    print(f"Fake ACME pump {serial} serving on {host}:{port} (invented protocol, synthetic data). Ctrl-C to stop.")
    async with srv:
        await asyncio.gather(srv.serve_forever(), clock())

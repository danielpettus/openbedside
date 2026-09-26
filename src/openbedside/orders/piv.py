"""Inbound auto-programming: IHE PIV, transaction PCD-03, message RGV^O15.

SIMULATOR ONLY IN THIS RELEASE. Sending a program to a real pump is a device
function that FDA regulates ("controls or alters" a connected device, MDDS
guidance, 28 Sept 2022). The listener parses and validates every order it receives,
but routes it only to an adapter whose supports_programming is True, and only the
simulator sets that. Everything else is refused with an explanation.

Simplification, stated plainly: IHE PIV returns an accept ACK and, separately, an
application acknowledgement RRG^O16. This release returns the RRG^O16 on the same
connection and sends no separate accept ACK.
"""
from __future__ import annotations

import logging
from typing import Iterable

from ..adapters.base import DeviceAdapter, NotSupported, ProgramRequest
from ..hl7.message import Message, msh, segment, control_id, escape

log = logging.getLogger("openbedside.piv")
PIV_OID = "1.3.6.1.4.1.19376.1.6.1.3.1"
VMD_CODE = "69986"


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_rgv(msg: Message) -> ProgramRequest:
    """Normalize an RGV^O15 into a ProgramRequest. Raises ValueError with a reason."""
    if msg.get("MSH", 9) != "RGV^O15^RGV_O15":
        raise ValueError(f"unsupported message type {msg.get('MSH', 9)!r}")
    if msg.get("MSH", 12) != "2.6":
        raise ValueError(f"unsupported HL7 version {msg.get('MSH', 12)!r}")
    if msg.get("MSH", 21, 3) != PIV_OID:
        raise ValueError("MSH-21 is not the IHE PIV profile")
    order_id = msg.get("ORC", 2, 1)
    if not order_id:
        raise ValueError("ORC-2 placer order number is required")
    patient = msg.get("PID", 3, 1) or None
    target = None
    for obx in msg.all("OBX"):
        code = obx[3].split("^")[0] if len(obx) > 3 else ""
        if code == VMD_CODE and len(obx) > 18 and obx[18]:
            target = obx[18].split("^")[0]
    if not target:
        raise ValueError("no target module: OBX with 69986 (VMD) and OBX-18 is required")
    drug = msg.get("RXG", 4, 2) or msg.get("RXG", 4, 1) or None
    rate = _num(msg.get("RXG", 15))
    rate_units = msg.get("RXG", 16, 1)
    if rate is not None and rate_units not in ("265266", "mL/h"):
        raise ValueError(f"rate units {rate_units!r} not supported; send mL/h")
    vtbi = _num(msg.get("RXG", 5))
    return ProgramRequest(order_id=order_id, device_id=target, module_id=target, patient_id=patient,
                          drug_name=drug, rate_ml_h=rate, vtbi_ml=vtbi)


def rrg(incoming: Message, code: str, text: str) -> str:
    lines = [msh("OpenBedside", "GATEWAY", incoming.get("MSH", 3), incoming.get("MSH", 4),
                 "RRG^O16^RRG_O16", control_id("RG"), incoming.get("MSH", 21), ack_accept="NE", ack_app="NE"),
             segment("MSA", code, incoming.control_id, escape(text))]
    if code != "AA":
        lines.append(segment("ERR", "", "", "", "E", "", "", "", escape(text)))
    return "\r".join(lines) + "\r"


class OrderListener:
    def __init__(self, adapters: Iterable[DeviceAdapter]):
        self.adapters = list(adapters)
        self.log: list[dict] = []

    async def handle(self, raw: str) -> str:
        try:
            msg = Message(raw)
        except ValueError as e:
            return rrg(Message("MSH|^~\\&|?|?|||||||||||||||||"), "AR", str(e))
        try:
            req = parse_rgv(msg)
        except ValueError as e:
            return self._reply(msg, "AR", str(e))
        adapter = next((a for a in self.adapters if a.owns(req.device_id)), None)
        if adapter is None:
            return self._reply(msg, "AE", f"unknown device or module {req.device_id}")
        if not adapter.supports_programming:
            return self._reply(msg, "AR", "auto-programming is simulator-only in this release")
        try:
            result = await adapter.program(req)
        except NotSupported as e:
            return self._reply(msg, "AR", str(e))
        return self._reply(msg, "AA" if result.accepted else "AE", result.reason)

    def _reply(self, msg: Message, code: str, text: str) -> str:
        self.log.append({"control_id": msg.control_id, "order": msg.get("ORC", 2, 1), "result": code, "text": text})
        self.log = self.log[-100:]
        log.info("PCD-03 %s -> %s %s", msg.control_id, code, text)
        return rrg(msg, code, text)


def build_test_order(module_id: str, drug: str, rate_ml_h: float, vtbi_ml: float,
                     patient_id: str = "TEST0001", order_id: str = "ORD0001") -> str:
    """A minimal RGV^O15 for exercising the simulator. Synthetic values only."""
    lines = [
        msh("TestEHR", "TESTFAC", "OpenBedside", "GATEWAY", "RGV^O15^RGV_O15", control_id("TO"),
            f"IHE_PCD_RGV_O15^IHE PCD^{PIV_OID}^ISO", ack_accept="AL", ack_app="AL"),
        segment("PID", "", "", f"{escape(patient_id)}^^^HOSP^MR"),
        segment("ORC", "RE", f"{escape(order_id)}^TestEHR"),
        segment("RXG", "1", "", "", f"^{escape(drug)}", f"{vtbi_ml:g}", "", "263762^MDC_DIM_MILLI_L^MDC",
                "", "", "", "", "", "", "", f"{rate_ml_h:g}", "265266^MDC_DIM_MILLI_L_PER_HR^MDC"),
        segment("RXR", "IV"),
        segment("OBX", "1", "", "69986^MDC_DEV_PUMP_INFUS_VMD^MDC", "1.1.0.0", "", "", "", "", "", "", "F",
                "", "", "", "", "", "", escape(module_id)),
    ]
    return "\r".join(lines) + "\r"

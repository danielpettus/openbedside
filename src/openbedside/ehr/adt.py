"""Inbound ADT: the hospital tells the gateway which patient is in which bed.

The gateway keeps identifiers and location only (PID-3, PV1-2, PV1-3, PV1-19).
Names, dates of birth and other demographics in the message are ignored and never
stored. Every message gets an application ACK; an event this listener does not use
is acknowledged AA and ignored, so the hospital's interface engine does not queue it.

Events used:
  A01 admit, A04 register, A08 update, A02 transfer   patient is at PV1-3
  A03 discharge, A11 cancel admit                     patient leaves the census
  A12 cancel transfer                                 patient returns to PV1-6 (prior location)
  A13 cancel discharge                                patient returns at PV1-3
  A40 merge                                           MRG-1 is replaced by PID-3
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..core.association import mask
from ..hl7.ack import build_ack
from ..hl7.message import Message, unescape
from ..store.registry import CensusEntry, Registry

log = logging.getLogger("openbedside.adt")
AT_LOCATION = {"A01", "A04", "A08", "A02", "A13"}
LEAVES = {"A03", "A11"}


def _pl(field: str) -> tuple[str, str, str, str]:
    parts = [unescape(p) for p in (field.split("^") + ["", "", "", ""])[:4]]
    return parts[0], parts[1], parts[2], parts[3]


class AdtListener:
    def __init__(self, registry: Registry, on_change: Optional[Callable[[], None]] = None):
        self.registry = registry
        self.on_change = on_change or (lambda: None)
        self.log: list[dict] = []

    def _note(self, event: str, pid: str, result: str) -> None:
        self.log.append({"event": event, "patient": mask(pid), "result": result})
        self.log = self.log[-50:]

    async def handle(self, raw: str) -> str:
        try:
            msg = Message(raw)
        except ValueError as e:
            log.warning("ADT: unparseable message: %s", e)
            return ("MSH|^~\\&|OpenBedside|GATEWAY|||||ACK|ERR|P|2.6\rMSA|AR||not an HL7 v2 message\r")
        mtype = msg.get("MSH", 9, 1)
        event = msg.get("MSH", 9, 2) or msg.get("EVN", 1)
        if mtype != "ADT":
            return build_ack(msg, "AR", f"expected ADT, got {mtype}")
        pid = unescape(msg.get("PID", 3, 1))
        authority = unescape(msg.get("PID", 3, 4))
        if not pid:
            return build_ack(msg, "AE", "PID-3 patient identifier is required")
        result = "ignored"
        if event in AT_LOCATION:
            unit, room, bed, fac = _pl(msg.get("PV1", 3))
            if event == "A08" and not (unit or bed):
                known = self.registry.census_find(pid, authority)
                if known:                               # an update with no location keeps the old one
                    unit, room, bed, fac = known.unit, known.room, known.bed, known.facility
            self.registry.census_upsert(CensusEntry(pid, authority, unescape(msg.get("PV1", 19, 1)),
                                                    msg.get("PV1", 2), unit, room, bed, fac, event))
            result = f"at {unit} {room} {bed}".strip()
        elif event in LEAVES:
            self.registry.census_remove(pid, authority)
            result = "left census"
        elif event == "A12":
            unit, room, bed, fac = _pl(msg.get("PV1", 6))
            known = self.registry.census_find(pid, authority)
            self.registry.census_upsert(CensusEntry(pid, authority, known.visit if known else "",
                                                    known.patient_class if known else msg.get("PV1", 2),
                                                    unit, room, bed, fac, event))
            result = f"back to {unit} {room} {bed}".strip()
        elif event == "A40":
            old = unescape(msg.get("MRG", 1, 1))
            known = self.registry.census_find(old) if old else None
            if known:
                self.registry.census_remove(old, known.authority)
                self.registry.census_upsert(CensusEntry(pid, authority or known.authority, known.visit,
                                                        known.patient_class, known.unit, known.room,
                                                        known.bed, known.facility, event))
                for e in self.registry.devices():         # manual associations follow the merge
                    m = self.registry.get_manual(e.key)
                    if m and m.patient_id == old:
                        self.registry.set_manual(e.key, pid, authority or m.authority, "A40 merge")
            result = f"merged {mask(old)}"
        self._note(event, pid, result)
        log.info("ADT %s %s: %s", event, mask(pid), result)
        if result != "ignored":
            self.on_change()
        return build_ack(msg, "AA", sending_app="OpenBedside")


def build_test_adt(event: str, patient_id: str, unit: str = "", room: str = "", bed: str = "",
                   authority: str = "HOSP", patient_class: str = "I", visit: str = "",
                   prior: str = "", merge_from: str = "") -> str:
    """A minimal ADT for testing. Synthetic identifiers only."""
    from ..hl7.message import control_id, escape, hl7_ts, segment
    ctrl = control_id("AD")
    lines = ["|".join(["MSH", "^~\\&", "TestADT", "TESTFAC", "OpenBedside", "GATEWAY", hl7_ts(), "",
                       f"ADT^{event}^ADT_A01", ctrl, "P", "2.6"]),
             segment("EVN", event, hl7_ts()),
             segment("PID", "1", "", f"{escape(patient_id)}^^^{escape(authority)}^MR", "", "TEST^PATIENT")]
    if merge_from:
        lines.append(segment("MRG", f"{escape(merge_from)}^^^{escape(authority)}^MR"))
    pl = "^".join(escape(x) for x in (unit, room, bed))
    lines.append(segment("PV1", "1", patient_class, pl, "", "", prior, *([""] * 12), escape(visit)))  # prior is a PL: unit^room^bed
    return "\r".join(lines) + "\r"

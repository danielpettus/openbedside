"""Who is this device's patient? Decided from authoritative sources only, with the
source recorded, and withheld when the sources disagree.

Sources, each allowed or not per device in the registry:
  device        the device itself reported an identifier (a wristband scanned at the pump)
  manual        a person associated them on the Patients page
  adt-location  the ADT feed has exactly one patient in the bed this device is registered to

Rules:
  1. Every allowed source that has an answer must agree. If they do not, no patient
     is sent and the conflict is shown on the Patients page.
  2. Two patients in one bed is a conflict, not a choice.
  3. If ADT location is the ONLY source and it would change the patient while the
     device is running (infusing, KVO, paused or alarming), the gateway withholds
     the patient and asks a person to confirm. A patient change mid-infusion is how
     wrong-patient charting starts: the pump went with the patient, and a new
     patient arrived in the old bed.
  4. With no answer, the patient is Unknown. Nothing is guessed.

What this does not do: know where a device really is. Association by ADT location
trusts the registry's location for the device. For devices that move with patients,
prefer the device-reported identifier or a manual association.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..model import Device, Location, PatientRef, PumpStatus
from ..store.registry import DeviceEntry, Registry

RUNNING = {PumpStatus.INFUSING, PumpStatus.KVO, PumpStatus.PAUSED, PumpStatus.ALARM}


def mask(pid: str) -> str:
    """For logs and read-only viewers: enough to tell two patients apart, not to identify one."""
    pid = pid or ""
    return ("•" * 3 + pid[-4:]) if len(pid) > 4 else "•" * 3


@dataclass
class Decision:
    state: str                                    # associated | none | conflict | review
    patient: Optional[PatientRef] = None
    detail: str = ""
    candidates: list[PatientRef] = field(default_factory=list)


def running(dev: Device) -> bool:
    return any(c.status in RUNNING for m in dev.modules for c in m.channels)


def decide(dev: Device, entry: DeviceEntry, reg: Registry, default_authority: str,
           confirmed: Optional[PatientRef]) -> Decision:
    """`confirmed` is the last patient this device was associated with and not yet
    released (see rule 3)."""
    cands: list[PatientRef] = []
    if entry.assoc_device and dev.reported_patient_id and dev.reported_patient_id.strip():
        pid = dev.reported_patient_id.strip()
        c = reg.census_find(pid)
        cands.append(PatientRef(pid, (c.authority if c else "") or default_authority, "device",
                                c.visit if c else "", c.patient_class if c else "",
                                Location(c.unit, c.room, c.bed, c.facility) if c else Location()))
    if entry.assoc_manual:
        m = reg.get_manual(entry.key)
        if m:
            c = reg.census_find(m.patient_id, m.authority)
            cands.append(PatientRef(m.patient_id, m.authority or (c.authority if c else "") or default_authority,
                                    "manual", c.visit if c else "", c.patient_class if c else "",
                                    Location(c.unit, c.room, c.bed, c.facility) if c else Location()))
    if entry.assoc_adt and entry.unit and entry.bed:
        here = reg.patients_at(entry.unit, entry.room, entry.bed)
        if len(here) > 1:
            return Decision("conflict", None, f"ADT places {len(here)} patients in {entry.unit} {entry.room} {entry.bed}".replace("  ", " "),
                            [PatientRef(c.patient_id, c.authority, "adt-location") for c in here])
        if here:
            c = here[0]
            cands.append(PatientRef(c.patient_id, c.authority or default_authority, "adt-location",
                                    c.visit, c.patient_class, Location(c.unit, c.room, c.bed, c.facility)))
    if not cands:
        return Decision("none", None, "no source has a patient for this device")
    first = cands[0]
    if any(not first.same_person(c) for c in cands[1:]):
        srcs = ", ".join(f"{c.source} {mask(c.patient_id)}" for c in cands)
        return Decision("conflict", None, f"sources disagree: {srcs}", cands)
    # agreement: merge, preferring the entry that carries visit and location (the census)
    richest = max(cands, key=lambda c: (bool(c.visit), c.location.known()))
    sources = "+".join(sorted({c.source for c in cands}))
    patient = PatientRef(first.patient_id, richest.authority or first.authority, sources,
                         richest.visit, richest.patient_class, richest.location)
    if (confirmed is not None and not confirmed.same_person(patient) and sources == "adt-location"
            and running(dev)):
        return Decision("review", None,
                        f"ADT now places {mask(patient.patient_id)} in this bed while the device is running "
                        f"for {mask(confirmed.patient_id)}. Confirm on the Patients page.", [patient])
    return Decision("associated", patient, f"from {sources}")

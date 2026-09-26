"""The internal schema. Every device adapter normalizes to these types, and everything
downstream (store, EHR, UI, orders) reads only these types. Vendor protocols stop at
the adapter.

Containment follows ISO/IEEE 11073-10201: one MDS (the device), one or more VMDs
(pump modules), channels inside each VMD. A two-module pump is ONE device with two
VMDs, never two devices. Flattening that hierarchy loses which module delivered
which drug, which is the whole meaning of the message.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PumpStatus(str, Enum):
    """Operational status of one delivery channel. Values chosen to map onto the
    IHE pump-status enumeration; see codes.PUMP_STATUS_TOKENS."""
    INFUSING = "infusing"
    PAUSED = "paused"
    IDLE = "idle"
    KVO = "kvo"          # keep vein open: still infusing, at a rate unrelated to the order
    COMPLETE = "complete"
    ALARM = "alarm"
    UNKNOWN = "unknown"


class SourceRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"   # piggyback: pauses the primary on a shared line


@dataclass
class Quantity:
    """A value with its unit. Units are carried as UCUM strings internally and mapped
    to MDC unit codes only at the HL7 edge. A number without a unit is refused."""
    value: float
    ucum: str

    def __post_init__(self) -> None:
        if not self.ucum:
            raise ValueError("a quantity without a unit is not allowed")


@dataclass
class InfusionSource:
    """What is in the bag or syringe feeding the channel."""
    role: SourceRole
    drug_name: Optional[str] = None
    concentration: Optional[Quantity] = None      # e.g. 4 mg/mL
    rate: Optional[Quantity] = None               # mL/h as programmed for this source
    dose_rate: Optional[Quantity] = None          # e.g. mcg/kg/min, only with its basis
    vtbi: Optional[Quantity] = None               # volume to be infused
    vtbi_remaining: Optional[Quantity] = None
    volume_delivered: Optional[Quantity] = None


@dataclass
class Channel:
    channel_id: str                                # vendor channel identifier, opaque
    status: PumpStatus = PumpStatus.UNKNOWN
    actual_rate: Optional[Quantity] = None         # what the channel is doing now
    sources: list[InfusionSource] = field(default_factory=list)

    def active_source(self) -> Optional[InfusionSource]:
        """The secondary runs while the primary waits. Report the one delivering."""
        for role in (SourceRole.SECONDARY, SourceRole.PRIMARY):
            for s in self.sources:
                if s.role == role and s.rate is not None:
                    return s
        return None


@dataclass
class Module:                                      # VMD
    module_id: str
    channels: list[Channel] = field(default_factory=list)


@dataclass
class Location:
    """Where a device, or a patient, is. Maps to the HL7 PL data type (PV1-3)."""
    unit: str = ""                                 # point of care, e.g. ICU
    room: str = ""
    bed: str = ""
    facility: str = ""

    def known(self) -> bool:
        return bool(self.unit or self.room or self.bed)

    def label(self) -> str:
        return " ".join(p for p in (self.unit, self.room, self.bed) if p)


@dataclass
class PatientRef:
    """The patient a device is associated with, and HOW that was decided.

    Set by the gateway's association step, never by an adapter. `source` says where
    the identity came from: "device" (the device itself reported it, e.g. a
    wristband scanned at the pump), "adt-location" (the hospital's ADT feed has
    exactly one patient in the bed the device is registered to), "manual" (a person
    associated them on the Patients page), or a combination when sources agree."""
    patient_id: str
    authority: str = ""                            # assigning authority, PID-3.4
    source: str = ""
    visit: str = ""                                # PV1-19, when the ADT feed supplied it
    patient_class: str = ""                        # PV1-2, when the ADT feed supplied it
    location: Location = field(default_factory=Location)

    def same_person(self, other: "PatientRef") -> bool:
        return (self.patient_id == other.patient_id
                and (not self.authority or not other.authority or self.authority == other.authority))


@dataclass
class Device:                                      # MDS
    device_id: str                                 # stable identifier from the device: serial or EUI-64
    vendor: str
    model: str
    modules: list[Module] = field(default_factory=list)
    # Set by an ADAPTER only when the device itself reports a patient identifier
    # (for example, the clinician scanned the wristband at the pump). Never guessed.
    reported_patient_id: Optional[str] = None
    patient_weight: Optional[Quantity] = None
    online: bool = True
    observed_at: datetime = field(default_factory=utcnow)
    device_clock: Optional[datetime] = None        # what the device says the time is
    kind: str = "infusion_pump"                    # what the adapter's data model describes
    # Set by the GATEWAY from the device registry and the association step:
    ehr_id: Optional[str] = None                   # the identifier the hospital scans and files by
    eui64: Optional[str] = None
    location: Location = field(default_factory=Location)
    patient: Optional[PatientRef] = None

    @property
    def key(self) -> str:
        """Vendor plus device id. Two manufacturers may both ship serial 12345."""
        return f"{self.vendor}|{self.device_id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        d["observed_at"] = self.observed_at.isoformat()
        d["device_clock"] = self.device_clock.isoformat() if self.device_clock else None
        return d


class EventKind(str, Enum):
    # The three IHE IPEC required events:
    DELIVERY_START = "delivery-start"
    DELIVERY_STOP = "delivery-stop"
    DELIVERY_COMPLETE = "delivery-complete"
    # Not IPEC events. IPEC has no rate change, no secondary-to-primary switchover
    # and no KVO transition. We DERIVE them from successive observations and label
    # them derived=True so no one mistakes them for device-reported events.
    RATE_CHANGE = "rate-change"
    KVO_START = "kvo-start"
    SECONDARY_TO_PRIMARY = "secondary-to-primary"
    COMMUNICATION_LOST = "communication-lost"
    COMMUNICATION_RESTORED = "communication-restored"
    # Gateway decisions about who the patient is. Not device events.
    PATIENT_ASSOCIATED = "patient-associated"
    PATIENT_CLEARED = "patient-cleared"
    ASSOCIATION_CONFLICT = "association-conflict"
    ASSOCIATION_REVIEW = "association-review"


@dataclass
class Event:
    kind: EventKind
    device_id: str
    module_id: str
    channel_id: str
    at: datetime
    derived: bool
    detail: dict = field(default_factory=dict)

"""The core. Takes normalized snapshots from any adapter, checks each device against
the registry, decides who its patient is, keeps current state, derives events, and
queues outbound messages. It knows nothing about vendor protocols and nothing about
the EHR beyond "hand this message to the outbox".
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import Optional

from ..hl7.pcd01 import Builder
from ..model import Channel, Device, Event, EventKind, Location, PatientRef, PumpStatus, SourceRole, utcnow
from ..store.outbox import Outbox
from .association import Decision, decide, mask, running

log = logging.getLogger("openbedside.engine")


class Engine:
    def __init__(self, outbox: Outbox, builder: Builder, destination: str,
                 periodic_seconds: float = 60.0, offline_after: float = 30.0,
                 registry=None, unknown_devices: str = "hold", default_authority: str = "HOSP"):
        self.outbox = outbox
        self.builder = builder
        self.destination = destination
        self.periodic = periodic_seconds
        self.offline_after = offline_after
        self.registry = registry                       # None: no registration, no association (tests, check)
        self.unknown_devices = unknown_devices         # hold | send
        self.default_authority = default_authority
        self.devices: dict[str, Device] = {}           # keyed by vendor|device_id
        self.last_heard: dict[str, float] = {}
        self.last_sent: dict[str, float] = {}
        self.events: list[Event] = []
        self.unregistered: dict[str, dict] = {}        # seen but not sent
        self.decisions: dict[str, Decision] = {}
        self.confirmed: dict[str, PatientRef] = {}

    # called by adapters
    def publish(self, dev: Device) -> None:
        key = dev.key
        entry = None
        if self.registry is not None:
            entry = self.registry.get(dev.vendor, dev.device_id)
            reason = ""
            if entry is None:
                reason = "not registered"
            elif entry.status != "active":
                reason = "retired"
            elif entry.device_type != dev.kind:
                reason = f"registered as {entry.device_type}, adapter reports {dev.kind}"
            if reason:
                self._note_unregistered(dev, reason)
                if reason != "not registered" or self.unknown_devices == "hold":
                    return
                entry = None
            self.unregistered.pop(key, None)
        prev = self.devices.get(key)
        was_offline = prev is not None and not prev.online
        self._apply_registry(dev, entry)
        new_events = self._associate(dev, entry)
        self.devices[key] = dev
        self.last_heard[key] = time.monotonic()
        new_events += self.derive(prev, dev)
        if was_offline:
            new_events.append(Event(EventKind.COMMUNICATION_RESTORED, dev.device_id, "", "", utcnow(), True))
        for e in new_events:
            self._record(e)
        due = time.monotonic() - self.last_sent.get(key, 0) >= self.periodic
        if new_events or due:
            self._queue(dev)

    def _note_unregistered(self, dev: Device, reason: str) -> None:
        u = self.unregistered.setdefault(dev.key, {"vendor": dev.vendor, "device_id": dev.device_id,
                                                   "model": dev.model, "kind": dev.kind, "count": 0,
                                                   "first_seen": utcnow().isoformat()})
        u.update(reason=reason, last_seen=utcnow().isoformat(), model=dev.model, kind=dev.kind,
                 modules=[m.module_id for m in dev.modules])
        u["count"] += 1

    def _apply_registry(self, dev: Device, entry) -> None:
        if entry is None:
            dev.ehr_id, dev.eui64, dev.location = dev.device_id, None, Location()
            return
        dev.ehr_id = entry.ehr_id()
        dev.eui64 = entry.eui64 or None
        dev.location = Location(entry.unit, entry.room, entry.bed, entry.facility)

    def _associate(self, dev: Device, entry) -> list[Event]:
        """Decide the patient and report any change as an event. Only registered
        devices can be associated; an unregistered device reports patient Unknown."""
        key = dev.key
        if self.registry is None or entry is None:
            d = Decision("none", None, "device is not registered" if self.registry is not None else "")
        else:
            d = decide(dev, entry, self.registry, self.default_authority, self.confirmed.get(key))
        prev = self.decisions.get(key)
        self.decisions[key] = d
        dev.patient = d.patient
        if d.state == "associated":
            self.confirmed[key] = d.patient
        elif d.state == "none" and not running(dev):
            self.confirmed.pop(key, None)
        events: list[Event] = []
        old_pid = prev.patient.patient_id if prev and prev.patient else None
        new_pid = d.patient.patient_id if d.patient else None
        changed_state = prev is None or prev.state != d.state
        if d.state == "associated" and (new_pid != old_pid or (prev and prev.patient and prev.patient.source != d.patient.source)):
            events.append(Event(EventKind.PATIENT_ASSOCIATED, dev.device_id, "", "", utcnow(), True,
                                {"patient": mask(new_pid), "source": d.patient.source}))
        elif d.state in ("conflict", "review") and (changed_state or d.detail != (prev.detail if prev else "")):
            kind = EventKind.ASSOCIATION_CONFLICT if d.state == "conflict" else EventKind.ASSOCIATION_REVIEW
            events.append(Event(kind, dev.device_id, "", "", utcnow(), True, {"detail": d.detail}))
        elif d.state == "none" and old_pid:
            events.append(Event(EventKind.PATIENT_CLEARED, dev.device_id, "", "", utcnow(), True,
                                {"was": mask(old_pid)}))
        return events

    def reassess(self) -> None:
        """Registry, census or a manual association changed: re-check every device we
        hold a snapshot for, and send at once if its registration or patient changed."""
        if self.registry is None:
            return
        for key, dev in list(self.devices.items()):
            entry = self.registry.get(dev.vendor, dev.device_id)
            if entry is None or entry.status != "active" or entry.device_type != dev.kind:
                if self.unknown_devices == "hold" or (entry is not None):
                    self.devices.pop(key, None)
                    self.decisions.pop(key, None)
                    self._note_unregistered(dev, "not registered" if entry is None else
                                            ("retired" if entry.status != "active" else "type mismatch"))
                    continue
                entry = None
            before = (dev.ehr_id, dev.eui64, asdict(dev.location), asdict(dev.patient) if dev.patient else None,
                      self.decisions.get(key).state if key in self.decisions else None)
            self._apply_registry(dev, entry)
            events = self._associate(dev, entry)
            for e in events:
                self._record(e)
            after = (dev.ehr_id, dev.eui64, asdict(dev.location), asdict(dev.patient) if dev.patient else None,
                     self.decisions[key].state)
            if events or before != after:
                self._queue(dev)

    def derive(self, prev: Optional[Device], cur: Device) -> list[Event]:
        """Compare two snapshots channel by channel. The three IHE delivery events are
        marked derived=False only if an adapter reports them as device events; here we
        infer everything, so everything is derived=True."""
        events: list[Event] = []
        prev_ch = {}
        if prev:
            for m in prev.modules:
                for c in m.channels:
                    prev_ch[(m.module_id, c.channel_id)] = c
        for m in cur.modules:
            for c in m.channels:
                p = prev_ch.get((m.module_id, c.channel_id))
                events.extend(self._channel_events(cur.device_id, m.module_id, p, c))
        return events

    def _channel_events(self, dev_id: str, mod_id: str, p: Optional[Channel], c: Channel) -> list[Event]:
        out: list[Event] = []
        now = utcnow()

        def ev(kind: EventKind, **detail) -> None:
            out.append(Event(kind, dev_id, mod_id, c.channel_id, now, True, detail))

        if p is None:
            if c.status == PumpStatus.INFUSING:
                ev(EventKind.DELIVERY_START, note="first observation already infusing")
            return out
        if p.status != c.status:
            if c.status == PumpStatus.INFUSING and p.status in (PumpStatus.IDLE, PumpStatus.PAUSED,
                                                                 PumpStatus.COMPLETE, PumpStatus.UNKNOWN):
                ev(EventKind.DELIVERY_START)
            elif c.status == PumpStatus.KVO:
                ev(EventKind.DELIVERY_COMPLETE)
                ev(EventKind.KVO_START, kvo_rate=c.actual_rate.value if c.actual_rate else None)
            elif c.status == PumpStatus.COMPLETE:
                ev(EventKind.DELIVERY_COMPLETE)
            elif c.status in (PumpStatus.PAUSED, PumpStatus.IDLE) and p.status == PumpStatus.INFUSING:
                ev(EventKind.DELIVERY_STOP)
        had_secondary = any(s.role == SourceRole.SECONDARY for s in p.sources)
        has_secondary = any(s.role == SourceRole.SECONDARY for s in c.sources)
        if had_secondary and not has_secondary and c.status == PumpStatus.INFUSING:
            ev(EventKind.SECONDARY_TO_PRIMARY)
        if (c.status == PumpStatus.INFUSING and p.status == PumpStatus.INFUSING and not (had_secondary and not has_secondary)
                and p.actual_rate and c.actual_rate and abs(p.actual_rate.value - c.actual_rate.value) > 1e-6):
            ev(EventKind.RATE_CHANGE, old=p.actual_rate.value, new=c.actual_rate.value, unit=c.actual_rate.ucum)
        return out

    def check_offline(self) -> None:
        now = time.monotonic()
        for key, heard in list(self.last_heard.items()):
            dev = self.devices.get(key)
            if dev is not None and dev.online and now - heard > self.offline_after:
                dev.online = False
                self._record(Event(EventKind.COMMUNICATION_LOST, dev.device_id, "", "", utcnow(), True,
                                   {"silent_seconds": round(now - heard)}))

    def _record(self, e: Event) -> None:
        self.events.append(e)
        self.events = self.events[-500:]
        self.outbox.log_event(e.at.isoformat(), e.device_id, e.module_id, e.channel_id,
                              e.kind.value, e.derived, json.dumps(e.detail, ensure_ascii=False))
        log.info("event %s %s %s %s", e.kind.value, e.device_id, e.module_id, e.detail or "")

    def _queue(self, dev: Device) -> None:
        try:
            ctrl, msg = self.builder.build(dev)
        except Exception as ex:                      # a build failure must be loud, not silent
            log.error("PCD-01 build failed for %s: %s", dev.device_id, ex)
            return
        self.outbox.put(self.destination, ctrl, msg)
        self.last_sent[dev.key] = time.monotonic()

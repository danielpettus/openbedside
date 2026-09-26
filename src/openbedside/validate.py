"""Checks a vendor adapter's output before anyone points it at a hospital.

Use it two ways:
  * from the command line:  openbedside check -c my-config.toml --seconds 60
  * inside your own tests:   problems = validate.check_device(device)

Each finding has a level:
  ERROR    the gateway will refuse or mangle this data; fix before going further
  WARNING  it will work, but a clinician or an interface analyst could be misled
  INFO     worth knowing
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from . import codes
from .hl7.pcd01 import Builder
from .model import Device, PumpStatus, Quantity, SourceRole

ERROR, WARNING, INFO = "ERROR", "WARNING", "INFO"
RATE_UNITS = {"mL/h"}
VOLUME_UNITS = {"mL"}


@dataclass
class Finding:
    level: str
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level:7} {self.code:24} {self.where}: {self.message}"


def _unit_ok(qty: Quantity | None, where: str, what: str, allowed: set[str] | None, out: list[Finding]) -> None:
    if qty is None:
        return
    if qty.value < 0:
        out.append(Finding(ERROR, "negative-value", where, f"{what} is negative ({qty.value})"))
    if allowed and qty.ucum not in allowed:
        out.append(Finding(ERROR, "wrong-unit", where, f"{what} must be in {sorted(allowed)}, got {qty.ucum!r}"))
        return
    try:
        codes.unit(qty.ucum)
    except codes.UnverifiedCode:
        out.append(Finding(ERROR, "unit-not-mappable", where,
                           f"{what} unit {qty.ucum!r} has no MDC code in codes.UNITS; it will not be sent"))


def check_device(dev: Device, clock_skew_warn: float = 60.0) -> list[Finding]:
    out: list[Finding] = []
    top = dev.device_id or "(no id)"
    if not dev.device_id:
        out.append(Finding(ERROR, "no-device-id", top, "device_id is empty"))
    if not dev.vendor or not dev.model:
        out.append(Finding(WARNING, "no-vendor-model", top, "vendor and model help the hospital identify the device"))
    if dev.observed_at.tzinfo is None:
        out.append(Finding(ERROR, "naive-time", top, "observed_at has no time zone"))
    if dev.device_clock is not None:
        if dev.device_clock.tzinfo is None:
            out.append(Finding(ERROR, "naive-device-clock", top, "device_clock has no time zone"))
        elif abs((dev.device_clock - dev.observed_at) / timedelta(seconds=1)) > clock_skew_warn:
            skew = (dev.device_clock - dev.observed_at).total_seconds()
            out.append(Finding(WARNING, "clock-skew", top, f"device clock differs from gateway by {skew:.0f} s"))
    if dev.patient is not None:
        out.append(Finding(ERROR, "patient-set-by-adapter", top,
                           "adapters never set Device.patient; put an identifier the device itself "
                           "reported in reported_patient_id, and the gateway decides"))
    if dev.reported_patient_id is not None:
        if not dev.reported_patient_id.strip():
            out.append(Finding(ERROR, "empty-patient-id", top, "reported_patient_id is blank; use None"))
        else:
            out.append(Finding(INFO, "patient-reported-by-device", top,
                               "device reports a patient id; used only where the registry allows it"))
    if not dev.modules:
        out.append(Finding(ERROR, "no-modules", top, "a device needs at least one module"))
    _unit_ok(dev.patient_weight, top, "patient weight", {"kg"}, out)
    ids = [m.module_id for m in dev.modules]
    if len(ids) != len(set(ids)):
        out.append(Finding(ERROR, "duplicate-module", top, "module ids must be unique within a device"))
    for m in dev.modules:
        where = f"{top}/{m.module_id}"
        if len(m.channels) != 1:
            out.append(Finding(ERROR, "channels-per-module", where,
                               f"exactly one delivery channel per module is supported, got {len(m.channels)}"))
            continue
        c = m.channels[0]
        if c.status == PumpStatus.UNKNOWN:
            out.append(Finding(WARNING, "status-unknown", where, "status UNKNOWN: check the state mapping"))
        if c.status in (PumpStatus.INFUSING, PumpStatus.KVO) and c.actual_rate is None:
            out.append(Finding(WARNING, "no-actual-rate", where, f"status {c.status.value} but no actual rate"))
        _unit_ok(c.actual_rate, where, "actual rate", RATE_UNITS, out)
        roles = [s.role for s in c.sources]
        if roles.count(SourceRole.PRIMARY) > 1 or roles.count(SourceRole.SECONDARY) > 1:
            out.append(Finding(ERROR, "duplicate-source", where, "at most one primary and one secondary"))
        if SourceRole.SECONDARY in roles and SourceRole.PRIMARY not in roles:
            out.append(Finding(WARNING, "secondary-without-primary", where, "a secondary normally runs over a primary"))
        for s in c.sources:
            sw = f"{where}/{s.role.value}"
            _unit_ok(s.rate, sw, "rate", RATE_UNITS, out)
            for name, qty in (("VTBI", s.vtbi), ("volume remaining", s.vtbi_remaining), ("volume delivered", s.volume_delivered)):
                _unit_ok(qty, sw, name, VOLUME_UNITS, out)
            _unit_ok(s.concentration, sw, "concentration", None, out)
            _unit_ok(s.dose_rate, sw, "dose rate", None, out)
            if s.vtbi and s.volume_delivered and s.volume_delivered.value > s.vtbi.value + 0.5:
                out.append(Finding(WARNING, "delivered-exceeds-vtbi", sw, "volume delivered is more than VTBI"))
            if s.dose_rate is not None:
                if s.concentration is None:
                    out.append(Finding(WARNING, "dose-without-basis", sw,
                                       "dose rate without concentration will be withheld"))
                if "/kg" in s.dose_rate.ucum and dev.patient_weight is None:
                    out.append(Finding(WARNING, "dose-without-weight", sw,
                                       "weight-based dose rate without patient weight will be withheld"))
        if c.status == PumpStatus.INFUSING and c.actual_rate is not None:
            active = c.active_source()
            if active and active.rate and abs(active.rate.value - c.actual_rate.value) > 1e-3:
                out.append(Finding(INFO, "actual-differs-from-program", where,
                                   f"actual {c.actual_rate.value} vs programmed {active.rate.value} on {active.role.value}"))
    try:
        Builder("CHECK", "CHECK", "CHECK", "CHECK").build(dev)
    except Exception as e:
        out.append(Finding(ERROR, "pcd01-build-failed", top, str(e)))
    return out

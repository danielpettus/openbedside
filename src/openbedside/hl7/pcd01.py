"""Build an IHE Devices PCD-01 (Communicate PCD Data) ORU^R01 from the internal model.

Containment is encoded in OBX-4 as MDS.VMD.CHANNEL.METRIC. For an infusion module:
  1.m.1   the delivery channel (what the module is doing now)
  1.m.2   the primary source (what is hanging)
  1.m.3   the secondary source, when a piggyback is programmed
Metric numbers inside a channel are this project's own and are listed below.

Profile identifier (MSH-21) for PCD-01 is 1.3.6.1.4.1.19376.1.6.1.1.1, an IHE
assigned OID seen in published vendor specifications. Verify against DEV TF-2
before claiming conformance.
"""
from __future__ import annotations

import logging

from .. import codes
from ..model import Device, Channel, InfusionSource, Quantity, SourceRole
from .message import msh, segment, control_id, escape, hl7_ts

log = logging.getLogger("openbedside.pcd01")
PROFILE = "IHE_PCD_ORU_R01^IHE PCD^1.3.6.1.4.1.19376.1.6.1.1.1^ISO"

# metric numbers, per channel type
DELIVERY_METRICS = {"status": 1, "flow": 2}
SOURCE_METRICS = {"drug": 1, "conc": 2, "rate": 3, "dose_rate": 4, "vtbi": 5,
                  "vtbi_remaining": 6, "vol_delivered": 7, "weight": 8}


def _units(q: Quantity) -> str:
    u = codes.unit(q.ucum)                          # refuses unverified units
    return f"{u.code}^{u.refid}^MDC^{escape(q.ucum)}^{escape(q.ucum)}^UCUM"


def _num(v: float) -> str:
    return ("%.4f" % v).rstrip("0").rstrip(".")


class Builder:
    def __init__(self, sending_app: str, sending_fac: str, receiving_app: str, receiving_fac: str):
        self.sa, self.sf, self.ra, self.rf = sending_app, sending_fac, receiving_app, receiving_fac

    def build(self, dev: Device) -> tuple[str, str]:
        """Return (control_id, message)."""
        ts = hl7_ts(dev.observed_at)
        ctrl = control_id("PD")
        out = [msh(self.sa, self.sf, self.ra, self.rf, "ORU^R01^ORU_R01", ctrl, PROFILE, when=dev.observed_at)]
        out.extend(self._patient_segments(dev))
        ehr_id = dev.ehr_id or dev.device_id
        dev_eq = self._equipment(ehr_id, dev.vendor, dev.eui64)
        out.append(segment("OBR", "1", "", f"{ctrl}^OpenBedside", codes.MDS_PUMP.cwe(), "", "", ts))
        obx: list[str] = []

        def add(code: str, sub: str, vtype: str = "", value: str = "", units: str = "",
                status: str = "R", equip: str = "") -> None:
            fields = [str(len(obx) + 1), vtype, code, sub, value, units,
                      "", "", "", "", status, "", "", ts, "", "", "", equip]
            obx.append(segment("OBX", *fields))

        add(codes.MDS_PUMP.cwe(), "1.0.0.0", status="X", equip=dev_eq)
        for m_idx, module in enumerate(dev.modules, start=1):
            if len(module.channels) != 1:
                raise ValueError(f"module {module.module_id}: exactly one delivery channel supported")
            ch = module.channels[0]
            add(codes.VMD_PUMP.cwe(), f"1.{m_idx}.0.0", status="X",
                equip=self._equipment(self._module_ehr_id(dev, module.module_id), dev.vendor, None))
            self._delivery(add, m_idx, ch)
            for src in ch.sources:
                c_idx = 2 if src.role == SourceRole.PRIMARY else 3
                self._source(add, m_idx, c_idx, src, dev)
        out.extend(obx)
        return ctrl, "\r".join(out) + "\r"

    @staticmethod
    def _equipment(ident: str, vendor: str, eui64: str | None) -> str:
        """OBX-18 Equipment Instance Identifier (EI): identifier ^ namespace (vendor)
        and, when the device has one, ^ EUI-64 ^ "EUI-64". This follows the form used
        in published vendor PCD-01 specifications; verify against DEV TF-2 Appendix B."""
        base = f"{escape(ident)}^{escape(vendor)}"
        return f"{base}^{eui64}^EUI-64" if eui64 else base

    @staticmethod
    def _module_ehr_id(dev: Device, module_id: str) -> str:
        """Module ids are built as <device id>-<channel>. When the hospital files by
        asset tag or EUI-64, the module carries that identifier instead."""
        ehr_id = dev.ehr_id or dev.device_id
        if ehr_id != dev.device_id and module_id.startswith(dev.device_id):
            return ehr_id + module_id[len(dev.device_id):]
        return module_id

    @staticmethod
    def _patient_segments(dev: Device) -> list[str]:
        """PID is required in PCD-01. When no patient is associated, the identifier
        is Unknown with identifier type U, the form used in published vendor PCD-01
        specifications. Nothing is guessed. PV1 is sent when a location or visit is
        known: from the ADT census for the associated patient, otherwise the
        device's registered location."""
        p = dev.patient
        if p:
            pid = segment("PID", "", "", f"{escape(p.patient_id)}^^^{escape(p.authority)}^MR", "", "^^^^^^U")
        else:
            pid = segment("PID", "", "", "Unknown^^^^U", "", "^^^^^^U")
        loc = p.location if (p and p.location.known()) else dev.location
        visit = p.visit if p else ""
        pclass = (p.patient_class if p else "") or "U"
        if not (loc.known() or visit):
            return [pid]
        pl = "^".join(escape(x) for x in (loc.unit, loc.room, loc.bed, loc.facility)).rstrip("^")
        pv1 = segment("PV1", "", pclass, pl, *([""] * 15), escape(visit))
        return [pid, pv1]

    def _delivery(self, add, m: int, ch: Channel) -> None:
        base = f"1.{m}.1"
        add(codes.CHAN_DELIVERY.cwe(), f"{base}.0", status="X")
        token = codes.PUMP_STATUS_TOKENS[ch.status.value]
        if codes.PUMP_STATUS_TOKEN_STATUS[ch.status.value] != codes.PUBLISHED_2013:
            log.debug("status token %s is UNVERIFIED against RTM", token)
        add(codes.PUMP_STAT.cwe(), f"{base}.{DELIVERY_METRICS['status']}", "CWE", f"^{token}^MDC")
        if ch.actual_rate is not None:
            add(codes.FLOW_FLUID_PUMP.cwe(), f"{base}.{DELIVERY_METRICS['flow']}", "NM",
                _num(ch.actual_rate.value), _units(ch.actual_rate))

    def _source(self, add, m: int, c: int, s: InfusionSource, dev: Device) -> None:
        base = f"1.{m}.{c}"
        add(codes.CHAN_SOURCE.cwe(), f"{base}.0", status="X")
        if s.drug_name:
            add(codes.DRUG_NAME_TYPE.cwe(), f"{base}.{SOURCE_METRICS['drug']}", "ST", escape(s.drug_name))
        pairs = [("conc", codes.CONC_DRUG, s.concentration), ("rate", codes.FLOW_FLUID_PUMP, s.rate),
                 ("vtbi", codes.VOL_FLUID_TBI, s.vtbi), ("vtbi_remaining", codes.VOL_FLUID_TBI_REMAIN, s.vtbi_remaining),
                 ("vol_delivered", codes.VOL_FLUID_DELIV, s.volume_delivered)]
        for key, code, q in pairs:
            if q is not None:
                add(code.cwe(), f"{base}.{SOURCE_METRICS[key]}", "NM", _num(q.value), _units(q))
        # A dose rate is only sent with the basis that produced it. Weight-based dosing
        # without weight and concentration cannot be recomputed or audited downstream.
        if s.dose_rate is not None:
            weight_based = "/kg" in s.dose_rate.ucum
            weight_units = None
            if weight_based and dev.patient_weight is not None:
                try:
                    weight_units = _units(dev.patient_weight)
                except codes.UnverifiedCode:
                    weight_units = None
            if s.concentration is None or (weight_based and weight_units is None):
                log.warning("dose rate withheld on %s: basis missing or its unit code unverified", base)
            else:
                add(codes.RATE_DOSE.cwe(), f"{base}.{SOURCE_METRICS['dose_rate']}", "NM",
                    _num(s.dose_rate.value), _units(s.dose_rate))
                if weight_based:
                    add(codes.ATTR_PT_WEIGHT.cwe(), f"{base}.{SOURCE_METRICS['weight']}", "NM",
                        _num(dev.patient_weight.value), weight_units)

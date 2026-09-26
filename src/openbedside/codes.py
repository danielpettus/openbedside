"""ISO/IEEE 11073-10101 (MDC) codes used at the HL7 edge.

Rule for this file: no code is typed from memory. Every entry carries where it came
from and a status. Before any production use, check each one against the current
Rosetta Terminology Mapping (RTM) tables and change the status to VERIFIED.

  PUBLISHED_2013  appears in a 2013 manufacturer's customer-facing HL7 specification
                  built against RTM (2011). A published fact, not a verification.
  UNVERIFIED      needed, not yet found in a source. The builder refuses to emit it.
"""
from dataclasses import dataclass

PUBLISHED_2013 = "PUBLISHED_2013"
VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"

SRC_2013 = "Vendor HL7 infusion status specification, 2013, against RTM 2011"


@dataclass(frozen=True)
class Mdc:
    code: int
    refid: str
    status: str
    source: str

    def cwe(self) -> str:
        return f"{self.code}^{self.refid}^MDC"


def _p(code: int, refid: str) -> Mdc:
    return Mdc(code, refid, PUBLISHED_2013, SRC_2013)


# Containment
MDS_PUMP = _p(69985, "MDC_DEV_PUMP_INFUS_MDS")
VMD_PUMP = _p(69986, "MDC_DEV_PUMP_INFUS_VMD")
CHAN_DELIVERY = _p(126978, "MDC_DEV_PUMP_INFUS_CHAN_DELIVERY")
CHAN_SOURCE = _p(126977, "MDC_DEV_PUMP_INFUS_CHAN_SOURCE")

# Metrics
PUMP_MODE = _p(184504, "MDC_PUMP_MODE")
PUMP_STAT = _p(184508, "MDC_PUMP_STAT")
FLOW_FLUID_PUMP = _p(157784, "MDC_FLOW_FLUID_PUMP")
VOL_FLUID_TBI = _p(157884, "MDC_VOL_FLUID_TBI")
VOL_FLUID_TBI_REMAIN = _p(157872, "MDC_VOL_FLUID_TBI_REMAIN")
VOL_FLUID_DELIV = _p(157864, "MDC_VOL_FLUID_DELIV")
DRUG_NAME_TYPE = _p(184330, "MDC_DRUG_NAME_TYPE")
CONC_DRUG = _p(157760, "MDC_CONC_DRUG")
RATE_DOSE = _p(157924, "MDC_RATE_DOSE")
INFUS_DOSE_DELIV = _p(195999, "MDC_INFUS_DOSE_DELIV")
ATTR_PT_WEIGHT = _p(68063, "MDC_ATTR_PT_WEIGHT")
AREA_BODY_SURF_ACTUAL = _p(188744, "MDC_AREA_BODY_SURF_ACTUAL")

# Units, keyed by the UCUM string used internally
UNITS: dict[str, Mdc] = {
    "mL": _p(263762, "MDC_DIM_MILLI_L"),
    "mL/h": _p(265266, "MDC_DIM_MILLI_L_PER_HR"),
    "mg": _p(263890, "MDC_DIM_MILLI_G"),
    "mg/mL": _p(264306, "MDC_DIM_MILLI_G_PER_ML"),
    "ug/kg/min": _p(264819, "MDC_DIM_MICRO_G_PER_KG_PER_MIN"),
    "[iU]/mL": _p(267744, "MDC_DIM_INTL_UNIT_PER_ML"),
    "[iU]/h": _p(267840, "MDC_DIM_INTL_UNIT_PER_HR"),
    "kg": Mdc(263875, "MDC_DIM_KILO_G", PUBLISHED_2013, "Vendor HL7 infusion orders specification, 2013"),
}

# Status tokens, carried as text in OBX-5 for MDC_PUMP_STAT
PUMP_STATUS_TOKENS = {
    "infusing": "pump-status-infusing",
    "paused": "pump-status-paused",
    "idle": "pump-status-idle",
    "kvo": "pump-status-kvo",
    "complete": "pump-status-complete",
    "alarm": "pump-status-alarm",
    "unknown": "pump-status-unknown",
}
# Only "pump-status-infusing" and "pump-mode-nominal" were seen in the 2013 source.
# The other tokens follow the same pattern and are UNVERIFIED until checked in RTM.
PUMP_STATUS_TOKEN_STATUS = {k: (PUBLISHED_2013 if k == "infusing" else UNVERIFIED)
                            for k in PUMP_STATUS_TOKENS}


class UnverifiedCode(Exception):
    pass


def unit(ucum: str) -> Mdc:
    u = UNITS.get(ucum)
    if u is None or u.status == UNVERIFIED:
        raise UnverifiedCode(f"no verified MDC unit code for UCUM '{ucum}'")
    return u

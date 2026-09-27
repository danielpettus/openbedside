"""A two-module infusion pump simulator. It exists so anyone can run the whole gateway
on a laptop with no hardware, and it is the ONLY adapter that accepts programming
requests in this release.

Scenario, in simulated time (speed-up set in config):
  module A  heparin 100 [iU]/mL at 12 mL/h; rate changed to 14 mL/h at minute 30
  module B  0.9% sodium chloride 1000 mL at 100 mL/h, with a vancomycin 4 mg/mL
            secondary (piggyback) at 166.6667 mL/h for 250 mL. The primary waits
            while the secondary runs, then resumes on its own.
When a source empties the channel drops to KVO, which is still infusing.
All values are synthetic. Nothing here is dosing guidance.

A different scenario can be given in the adapter's config, for demonstrations:

  [[adapters]]
  type = "simulator"
  device_id = "SIM-0001"
  rate_change = false
  modules = [
    { id = "A", status = "infusing",
      primary = { drug = "cefazolin 1 g in sodium chloride 0.9% 100 mL", conc = [10, "mg/mL"], rate = 100, vtbi = 100 } },
    { id = "B", status = "idle" },
  ]

Each source takes drug, rate (mL/h), vtbi (mL), and optionally conc = [value, "unit"],
dose_unit and delivered. A module takes id, status (infusing or idle), primary and
secondary.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from ..model import (Channel, Device, InfusionSource, Module, PumpStatus, Quantity,
                     SourceRole, utcnow)
from .base import DeviceAdapter, ProgramRequest, ProgramResult


@dataclass
class _Src:
    role: SourceRole
    drug: str
    conc: Optional[Quantity]
    rate: float                  # mL/h
    vtbi: float                  # mL
    delivered: float = 0.0
    dose_unit: Optional[str] = None   # e.g. "[iU]/h"; dose = rate * conc

    @property
    def remaining(self) -> float:
        return max(0.0, self.vtbi - self.delivered)


@dataclass
class _Mod:
    module_id: str
    primary: Optional[_Src] = None
    secondary: Optional[_Src] = None
    status: PumpStatus = PumpStatus.IDLE
    kvo_rate: float = 1.0
    events: list = field(default_factory=list)


class SimulatorAdapter(DeviceAdapter):
    name = "simulator"
    supports_programming = True

    def __init__(self, config: dict, publish):
        super().__init__(config, publish)
        self.device_id = config.get("device_id", "SIM-0001")
        self.speed = float(config.get("speed", 60))           # simulated seconds per real second
        self.tick = float(config.get("tick_seconds", 1.0))
        self.sim_elapsed = 0.0
        if config.get("modules"):
            self.mods = {str(m["id"]): self._module(m) for m in config["modules"]}
            self.rate_change_done = not config.get("rate_change", False)
        else:
            d = self.device_id
            self.mods = {
                "A": _Mod(f"{d}-A", primary=_Src(SourceRole.PRIMARY, "heparin", Quantity(100, "[iU]/mL"),
                                                  12.0, 250.0, dose_unit="[iU]/h"), status=PumpStatus.INFUSING),
                "B": _Mod(f"{d}-B",
                          primary=_Src(SourceRole.PRIMARY, "sodium chloride 0.9%", None, 100.0, 1000.0),
                          secondary=_Src(SourceRole.SECONDARY, "vancomycin", Quantity(4, "mg/mL"), 166.6667, 250.0),
                          status=PumpStatus.INFUSING),
            }
            self.rate_change_done = not config.get("rate_change", True)

    def _source(self, role: SourceRole, spec: Optional[dict]) -> Optional[_Src]:
        if not spec:
            return None
        conc = spec.get("conc")
        return _Src(role, str(spec.get("drug", "unnamed")),
                    Quantity(float(conc[0]), str(conc[1])) if conc else None,
                    float(spec["rate"]), float(spec["vtbi"]), float(spec.get("delivered", 0.0)),
                    spec.get("dose_unit"))

    def _module(self, spec: dict) -> _Mod:
        primary = self._source(SourceRole.PRIMARY, spec.get("primary"))
        secondary = self._source(SourceRole.SECONDARY, spec.get("secondary"))
        status = PumpStatus(spec.get("status", "infusing" if (primary or secondary) else "idle"))
        return _Mod(f"{self.device_id}-{spec['id']}", primary=primary, secondary=secondary, status=status)

    def owns(self, device_id: str) -> bool:
        """True for this device's id or any of its module ids."""
        return device_id == self.device_id or any(device_id == m.module_id for m in self.mods.values())

    # ---- simulation ---------------------------------------------------------
    def step(self, sim_seconds: float) -> None:
        self.sim_elapsed += sim_seconds
        if not self.rate_change_done and self.sim_elapsed >= 30 * 60 and "A" in self.mods and self.mods["A"].primary:
            self.mods["A"].primary.rate = 14.0
            self.rate_change_done = True
        for m in self.mods.values():
            if m.status not in (PumpStatus.INFUSING, PumpStatus.KVO):
                continue
            if m.status == PumpStatus.KVO:
                continue                                     # KVO volume not tracked per source
            active = m.secondary if m.secondary else m.primary
            if active is None:
                m.status = PumpStatus.IDLE
                continue
            active.delivered = min(active.vtbi, active.delivered + active.rate * sim_seconds / 3600.0)
            if active.remaining <= 0:
                if active is m.secondary:
                    m.secondary = None                       # primary resumes by itself
                else:
                    m.status = PumpStatus.KVO

    def snapshot(self) -> Device:
        modules = []
        for m in self.mods.values():
            sources = []
            for s in (m.primary, m.secondary):
                if s is None:
                    continue
                dose = None
                if s.dose_unit and s.conc is not None:
                    dose = Quantity(round(s.rate * s.conc.value, 4), s.dose_unit)
                sources.append(InfusionSource(
                    role=s.role, drug_name=s.drug, concentration=s.conc,
                    rate=Quantity(s.rate, "mL/h"), dose_rate=dose, vtbi=Quantity(s.vtbi, "mL"),
                    vtbi_remaining=Quantity(round(s.remaining, 3), "mL"),
                    volume_delivered=Quantity(round(s.delivered, 3), "mL")))
            if m.status == PumpStatus.KVO:
                actual = Quantity(m.kvo_rate, "mL/h")
            elif m.status == PumpStatus.INFUSING:
                active = m.secondary or m.primary
                actual = Quantity(active.rate, "mL/h") if active else None
            else:
                actual = None
            modules.append(Module(m.module_id, [Channel(f"{m.module_id}-CH1", m.status, actual, sources)]))
        now = utcnow()
        return Device(self.device_id, "OpenBedside-Sim", "SIM-2CH", modules,
                      observed_at=now, device_clock=now + timedelta(seconds=0))

    async def run(self) -> None:
        self.publish(self.snapshot())
        while True:
            await asyncio.sleep(self.tick)
            self.step(self.tick * self.speed)
            self.publish(self.snapshot())

    # ---- programming (simulator only) -----------------------------------------
    async def program(self, req: ProgramRequest) -> ProgramResult:
        idle = (PumpStatus.IDLE, PumpStatus.KVO, PumpStatus.COMPLETE)
        if req.module_id:
            target = next((m for k, m in self.mods.items() if req.module_id in (k, m.module_id)), None)
            if target is None:
                return ProgramResult(False, f"unknown module {req.module_id}")
        else:
            target = next((m for m in self.mods.values() if m.status in idle), None)
            if target is None:
                return ProgramResult(False, "no idle module on this device")
        if target.status == PumpStatus.INFUSING:
            return ProgramResult(False, "module is infusing; stop it before programming")
        if not req.rate_ml_h or req.rate_ml_h <= 0:
            return ProgramResult(False, "rate missing or not positive")
        if not req.vtbi_ml or req.vtbi_ml <= 0:
            return ProgramResult(False, "volume to be infused missing or not positive")
        # A real pump would now check its drug library and ask the clinician to confirm
        # at the device. The simulator just accepts, to exercise the message path.
        target.primary = _Src(SourceRole.PRIMARY, req.drug_name or "unnamed", None, req.rate_ml_h, req.vtbi_ml)
        target.secondary = None
        target.status = PumpStatus.INFUSING
        self.publish(self.snapshot())
        return ProgramResult(True, f"programmed on {target.module_id} (simulator)")

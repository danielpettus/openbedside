"""Tests for the pieces a device manufacturer uses: the stream adapter base, the worked
ACME example, the checker and the scaffold."""
import asyncio
import os
import subprocess
import sys
import tempfile

from openbedside import validate
from openbedside.adapters.simulator import SimulatorAdapter
from openbedside.examples import acme_json
from openbedside.model import PumpStatus, Quantity, SourceRole
from openbedside.scaffold import make


def test_simulator_output_passes_checker():
    dev = SimulatorAdapter({}, lambda d: None).snapshot()
    assert not [f for f in validate.check_device(dev) if f.level == validate.ERROR]


def test_checker_catches_bad_units_and_missing_basis():
    dev = SimulatorAdapter({}, lambda d: None).snapshot()
    src = dev.modules[0].channels[0].sources[0]
    src.rate = Quantity(12, "mL/min")              # wrong unit for a rate
    src.concentration = None                       # dose rate loses its basis
    codes_found = {f.code for f in validate.check_device(dev)}
    assert "wrong-unit" in codes_found
    assert "dose-without-basis" in codes_found


def test_acme_normalize_maps_vocabulary():
    raw = {"type": "status", "serial": "ACME-1", "time": "2026-09-26T15:00:00+00:00", "channels": [
        {"ch": "L", "state": "RUN", "drug": "heparin", "conc": [100, "units/mL"], "rate": 12, "vtbi": 250,
         "vi": 20, "dose": [1200, "units/hr"], "kvo_rate": 1},
        {"ch": "R", "state": "RUN", "drug": "saline", "conc": None, "rate": 75, "vtbi": 500, "vi": 5, "kvo_rate": 1,
         "sec": {"drug": "cefazolin", "conc": [20, "mg/mL"], "rate": 100, "vtbi": 50, "vi": 10}}]}
    dev = acme_json.AcmeJsonAdapter({}, lambda d: None).normalize(raw)
    left, right = dev.modules[0].channels[0], dev.modules[1].channels[0]
    assert left.status == PumpStatus.INFUSING and left.sources[0].concentration.ucum == "[iU]/mL"
    assert right.actual_rate.value == 100          # the secondary is what is running
    assert [s.role for s in right.sources] == [SourceRole.PRIMARY, SourceRole.SECONDARY]
    assert not [f for f in validate.check_device(dev) if f.level == validate.ERROR]


def test_acme_ignores_non_status_messages():
    assert acme_json.AcmeJsonAdapter({}, lambda d: None).normalize({"type": "hello"}) is None


def test_stream_adapter_against_fake_pump_end_to_end():
    async def scenario():
        port = 17001
        pump = asyncio.create_task(acme_json.fake_pump("127.0.0.1", port, interval=0.2))
        await asyncio.sleep(0.2)
        got = []
        adapter = acme_json.AcmeJsonAdapter({"host": "127.0.0.1", "port": port}, got.append)
        task = asyncio.create_task(adapter.run())
        await asyncio.sleep(1.0)
        task.cancel()
        pump.cancel()
        await asyncio.gather(task, pump, return_exceptions=True)
        return got, adapter
    got, adapter = asyncio.run(scenario())
    assert len(got) >= 2 and adapter.messages >= 2 and adapter.skipped == 0
    assert adapter.owns("ACME-7731") and adapter.owns("ACME-7731-L")


def test_stream_adapter_reconnects_when_device_absent():
    async def scenario():
        adapter = acme_json.AcmeJsonAdapter({"host": "127.0.0.1", "port": 1}, lambda d: None)
        adapter.reconnect_min = 0.05
        task = asyncio.create_task(adapter.run())
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return adapter
    adapter = asyncio.run(scenario())
    assert adapter.connected is False and adapter.last_error


def test_scaffold_writes_a_working_starter():
    d = tempfile.mkdtemp()
    target = make("Widget Co", os.path.join(d, "widget"))
    files = sorted(os.listdir(target))
    assert files == ["MAPPING.md", "test_widget_co_adapter.py", "widget_co.toml", "widget_co_adapter.py"]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([target, os.path.join(os.path.dirname(__file__), "..", "src")]))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", target], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_command_passes_on_acme_example():
    async def pump():
        await acme_json.fake_pump("127.0.0.1", 17002, interval=0.2)
    d = tempfile.mkdtemp()
    cfg = os.path.join(d, "c.toml")
    with open(cfg, "w") as f:
        f.write('[[adapters]]\ntype = "acme-example"\nhost = "127.0.0.1"\nport = 17002\n')
    from openbedside.__main__ import check, load_config

    async def scenario():
        t = asyncio.create_task(pump())
        await asyncio.sleep(0.2)
        rc = await check(load_config(cfg), 1.0, False)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        return rc
    assert asyncio.run(scenario()) == 0


def test_simulator_scenario_from_config():
    from openbedside.adapters.simulator import SimulatorAdapter
    from openbedside.model import PumpStatus
    a = SimulatorAdapter({"device_id": "SIM-0009", "rate_change": False, "modules": [
        {"id": "A", "status": "infusing",
         "primary": {"drug": "cefazolin 1 g in sodium chloride 0.9% 100 mL", "conc": [10, "mg/mL"], "rate": 100, "vtbi": 100}},
        {"id": "B", "status": "idle"}]}, lambda d: None)
    d = a.snapshot()
    assert [m.module_id for m in d.modules] == ["SIM-0009-A", "SIM-0009-B"]
    assert d.modules[0].channels[0].actual_rate.value == 100
    assert d.modules[1].channels[0].status == PumpStatus.IDLE
    a.step(3600)                                   # one simulated hour: the 100 mL bag is done
    assert a.mods["A"].status == PumpStatus.KVO
    assert all(f.level != validate.ERROR for f in validate.check_device(a.snapshot()))

"""Command line.

  openbedside demo                     gateway + simulator + stand-in EHR, one window
  openbedside run      [-c config.toml]  run the gateway
  openbedside ehr-sim  [--port 6661]     a stand-in EHR interface engine
  openbedside send-order --module SIM-0001-A --drug heparin --rate 10 --vtbi 100
  openbedside check    -c config.toml    check an adapter's output before a hospital sees it
  openbedside new-adapter NAME           write a starter adapter folder for a device vendor
  openbedside fake-pump                  run the fictional ACME pump used by the worked example
  openbedside set-password [-c config]   set the admin password for the Devices and Patients pages
  openbedside send-adt --event A01 --mrn TEST0001 --unit ICU --room 101 --bed A   a test ADT message
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import sys
import tomllib

from . import __version__
from .adapters.base import DeviceAdapter
from .adapters.simulator import SimulatorAdapter
from .core.engine import Engine
from .ehr.adt import AdtListener, build_test_adt
from .ehr.sender import Sender
from .hl7 import mllp
from .hl7.ack import build_ack
from .hl7.message import Message
from .hl7.pcd01 import Builder
from .orders.piv import OrderListener, build_test_order
from .store.outbox import Outbox
from .store.registry import DeviceEntry, Registry
from .ui.server import Api, serve as serve_ui

DEFAULTS = {
    "gateway": {"name": "OpenBedside", "facility": "GATEWAY", "database": "openbedside.db",
                "periodic_seconds": 60, "offline_after_seconds": 30},
    "ehr": {"host": "127.0.0.1", "port": 6661, "receiving_app": "EHR", "receiving_facility": "HOSP"},
    "orders": {"enabled": True, "host": "127.0.0.1", "port": 6662},
    "ui": {"host": "127.0.0.1", "port": 8080},
    "registry": {"unknown_devices": "hold"},
    "patients": {"assigning_authority": "HOSP"},
    "adt": {"enabled": True, "host": "127.0.0.1", "port": 6663},
    "adapters": [{"type": "simulator", "device_id": "SIM-0001", "speed": 60, "tick_seconds": 1,
                  "preregister": {"unit": "ICU", "room": "101", "bed": "A", "assoc_adt": True}}],
}


def load_config(path: str | None) -> dict:
    cfg = {k: (v.copy() if isinstance(v, dict) else list(v)) for k, v in DEFAULTS.items()}
    if path:
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


BUILTIN = {"acme-example": "openbedside.examples.acme_json:AcmeJsonAdapter"}


def add_adapter_paths(cfg: dict, base: str | None) -> None:
    """[gateway] adapter_paths lets a vendor keep its adapter in its own folder."""
    root = os.path.dirname(os.path.abspath(base)) if base else os.getcwd()
    for p in cfg["gateway"].get("adapter_paths", []):
        full = os.path.abspath(os.path.join(root, os.path.expanduser(p)))
        if full not in sys.path:
            sys.path.insert(0, full)


def make_adapter(spec: dict, publish) -> DeviceAdapter:
    kind = spec.get("type", "simulator")
    if kind == "simulator":
        return SimulatorAdapter(spec, publish)
    kind = BUILTIN.get(kind, kind)
    # Custom adapters: type = "package.module:ClassName". See docs/ADAPTER-SDK.md.
    mod_name, _, cls_name = kind.partition(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    if not issubclass(cls, DeviceAdapter):
        raise TypeError(f"{kind} is not a DeviceAdapter")
    return cls(spec, publish)


def preregister(cfg: dict, registry: Registry) -> None:
    """Simulator devices register themselves (location from the adapter's `preregister`
    table) so the demo works out of the box. Real devices are registered on the
    Devices page. An existing entry is never overwritten."""
    for spec in cfg["adapters"]:
        if spec.get("type", "simulator") != "simulator":
            continue
        dev_id = spec.get("device_id", "SIM-0001")
        if registry.get("OpenBedside-Sim", dev_id):
            continue
        extra = {k: v for k, v in dict(spec.get("preregister", {})).items() if k in DeviceEntry.__dataclass_fields__}
        registry.save_device(DeviceEntry(vendor="OpenBedside-Sim", device_id=dev_id, model="SIM-2CH",
                                         notes="simulator, registered automatically", **extra))


async def run(cfg: dict) -> None:
    g, e, o, u = cfg["gateway"], cfg["ehr"], cfg["orders"], cfg["ui"]
    adt_cfg, authority = cfg["adt"], cfg["patients"].get("assigning_authority", "HOSP")
    outbox = Outbox(g["database"])
    registry = Registry(g["database"])
    preregister(cfg, registry)
    dest = f"{e['host']}:{e['port']}"
    unknown = cfg["registry"].get("unknown_devices", "hold")
    if unknown not in ("hold", "send"):
        raise SystemExit('[registry] unknown_devices must be "hold" or "send"')
    engine = Engine(outbox, Builder(g["name"], g["facility"], e["receiving_app"], e["receiving_facility"]),
                    dest, float(g["periodic_seconds"]), float(g["offline_after_seconds"]),
                    registry=registry, unknown_devices=unknown, default_authority=authority)
    adapters = [make_adapter(spec, engine.publish) for spec in cfg["adapters"]]
    sender = Sender(outbox, dest, e["host"], int(e["port"]))
    listener = OrderListener(adapters)
    loop = asyncio.get_running_loop()
    changed = lambda: loop.call_soon_threadsafe(engine.reassess)     # the web page runs in its own thread
    adt = AdtListener(registry, engine.reassess)

    def state() -> dict:
        return {"version": __version__,
                "devices": [d.to_dict() for d in list(engine.devices.values())],
                "events": outbox.recent_events(30),
                "outbox": {"counts": outbox.counts(), "recent": outbox.recent(15)},
                "ehr": {"connected": sender.connected, "last_error": sender.last_error, "destination": dest},
                "orders": list(reversed(listener.log[-20:])),
                "adt_log": adt.log[-20:],
                "adapters": [{"name": a.name, "connected": getattr(a, "connected", True),
                              "messages": getattr(a, "messages", None), "skipped": getattr(a, "skipped", None),
                              "last_error": getattr(a, "last_error", "")} for a in adapters]}

    serve_ui(u["host"], int(u["port"]), Api(registry, state, engine, changed, authority))
    tasks = [asyncio.create_task(a.run(), name=f"adapter:{a.name}") for a in adapters]
    tasks.append(asyncio.create_task(sender.run(), name="sender"))
    if o.get("enabled", True):
        await mllp.serve(o["host"], int(o["port"]), listener.handle)
    if adt_cfg.get("enabled", True):
        await mllp.serve(adt_cfg["host"], int(adt_cfg["port"]), adt.handle)

    async def watchdog() -> None:
        while True:
            await asyncio.sleep(5)
            engine.check_offline()
    tasks.append(asyncio.create_task(watchdog(), name="watchdog"))
    print(f"OpenBedside {__version__} running.")
    print(f"  UI            http://{u['host']}:{u['port']}   (Activity, Devices, Patients)")
    print(f"  EHR outbound  {dest} (MLLP)")
    if adt_cfg.get("enabled", True):
        print(f"  ADT in        {adt_cfg['host']}:{adt_cfg['port']} (MLLP)")
    if o.get("enabled", True):
        print(f"  Orders in     {o['host']}:{o['port']} (MLLP, simulator only)")
    print(f"  Unregistered devices are {'held, not sent' if unknown == 'hold' else 'sent without a patient'}.")
    if not registry.has_admin_password():
        print("  No admin password: changes only from this computer. Set one: openbedside set-password")
    print("  Not a medical device. Not for clinical use. Synthetic patient data only. Ctrl-C to stop.")
    await asyncio.gather(*tasks)


async def ehr_sim(host: str, port: int, reject_every: int, quiet: bool) -> None:
    """Pretends to be a hospital interface engine: shows what arrives, replies AA."""
    count = 0

    async def handle(raw: str) -> str:
        nonlocal count
        count += 1
        msg = Message(raw)
        reject = bool(reject_every and count % reject_every == 0)
        if quiet:
            print(f"  EHR received #{count} {msg.type} {msg.control_id} -> {'AE' if reject else 'AA'}")
        else:
            print(f"\n--- message {count}: {msg.type} {msg.control_id}")
            for seg in msg.segments:
                print("   ", seg)
        if reject:
            if not quiet:
                print("    -> replying AE (simulated error)")
            return build_ack(msg, "AE", "simulated receiver error", sending_app="EHR-SIM")
        return build_ack(msg, "AA", sending_app="EHR-SIM")

    srv = await mllp.serve(host, port, handle)
    print(f"EHR simulator listening on {host}:{port}.")
    async with srv:
        await srv.serve_forever()


async def demo(cfg: dict) -> None:
    """Everything in one window: stand-in EHR, gateway, pump simulator, and one
    synthetic ADT admit into the simulator's bed so association can be seen working."""
    e, a = cfg["ehr"], cfg["adt"]
    ehr = asyncio.create_task(ehr_sim(e["host"], int(e["port"]), 0, quiet=True))
    await asyncio.sleep(0.3)                     # let the stand-in EHR start listening first
    gw = asyncio.create_task(run(cfg))

    async def admit() -> None:
        pre = next((s.get("preregister", {}) for s in cfg["adapters"] if s.get("type", "simulator") == "simulator"), {})
        if not (a.get("enabled", True) and pre.get("assoc_adt")):
            return
        await asyncio.sleep(3)
        msg = build_test_adt("A01", "TEST0001", pre.get("unit", ""), pre.get("room", ""), pre.get("bed", ""),
                             cfg["patients"].get("assigning_authority", "HOSP"), visit="V0001")
        try:
            await mllp.send(a["host"], int(a["port"]), msg)
            print("  Demo: synthetic ADT admit of TEST0001 to", pre.get("unit"), pre.get("room"), pre.get("bed"))
        except OSError as ex:
            print("  Demo: could not send the synthetic ADT:", ex)
    await asyncio.gather(ehr, gw, admit())


async def check(cfg: dict, seconds: float, show_hl7: bool) -> int:
    """Run the configured adapters without sending anything, and check what they publish."""
    from .validate import check_device, ERROR, WARNING
    import time
    snaps: list = []
    heard: dict[str, list[float]] = {}

    def collect(dev):
        snaps.append(dev)
        heard.setdefault(dev.key, []).append(time.monotonic())

    adapters = [make_adapter(spec, collect) for spec in cfg["adapters"]]
    print(f"Checking {len(adapters)} adapter(s) for {seconds:.0f} s. Nothing is sent to any EHR.")
    tasks = [asyncio.create_task(a.run()) for a in adapters]
    await asyncio.sleep(seconds)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    print(f"\nSnapshots received: {len(snaps)} from {len(heard)} device(s)")
    for a in adapters:
        extra = ""
        if hasattr(a, "messages"):
            extra = f" messages={a.messages} skipped={a.skipped} last_error={a.last_error or '-'}"
        print(f"  adapter {a.name}:{extra}")
    if not snaps:
        print("\nFAIL: no snapshots. Is the device (or its test harness) running and reachable?")
        return 1
    limit = float(cfg["gateway"].get("offline_after_seconds", 30))
    seen: dict[tuple, list] = {}
    for dev in snaps:
        for f in check_device(dev):
            seen.setdefault((f.level, f.code, f.where), []).append(f)
    for dev_id, times in heard.items():
        gaps = [b - a for a, b in zip(times, times[1:])]
        if gaps and max(gaps) > limit:
            print(f"  WARNING longest silence from {dev_id} was {max(gaps):.0f} s, over offline_after_seconds={limit:.0f}")
    errors = sum(1 for k in seen if k[0] == ERROR)
    warnings = sum(1 for k in seen if k[0] == WARNING)
    print(f"\nFindings: {errors} error(s), {warnings} warning(s), {len(seen) - errors - warnings} info")
    for (level, code, where), items in sorted(seen.items()):
        print(f"  {items[0]}   [x{len(items)}]")
    if show_hl7:
        print("\nPCD-01 the EHR would receive for the latest snapshot:\n")
        print(Builder("OpenBedside", "GATEWAY", "EHR", "HOSP").build(snaps[-1])[1].replace("\r", "\n"))
    print("\nPASS" if errors == 0 else "\nFAIL: fix the errors above before testing with an EHR.")
    return 0 if errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="openbedside", description=f"OpenBedside {__version__}. Not a medical device.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="gateway, pump simulator and stand-in EHR in one window")
    d.add_argument("-c", "--config", default=os.environ.get("OPENBEDSIDE_CONFIG"))
    r = sub.add_parser("run", help="run the gateway")
    r.add_argument("-c", "--config", default=os.environ.get("OPENBEDSIDE_CONFIG"))
    s = sub.add_parser("ehr-sim", help="run a stand-in EHR interface engine")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=6661)
    s.add_argument("--reject-every", type=int, default=0, help="reply AE to every Nth message")
    s.add_argument("--quiet", action="store_true", help="one line per message")
    so = sub.add_parser("send-order", help="send a test PCD-03 order to the gateway (simulator only)")
    so.add_argument("--host", default="127.0.0.1")
    so.add_argument("--port", type=int, default=6662)
    so.add_argument("--module", required=True)
    so.add_argument("--drug", default="test drug")
    so.add_argument("--rate", type=float, required=True, help="mL/h")
    so.add_argument("--vtbi", type=float, required=True, help="mL")
    ck = sub.add_parser("check", help="check an adapter's output (nothing is sent)")
    ck.add_argument("-c", "--config", required=True)
    ck.add_argument("--seconds", type=float, default=30)
    ck.add_argument("--show-hl7", action="store_true", help="print the PCD-01 for the latest snapshot")
    na = sub.add_parser("new-adapter", help="write a starter adapter folder for a device vendor")
    na.add_argument("name", help="short name, e.g. acme")
    na.add_argument("--dir", help="where to write it (default: ./NAME-adapter)")
    sp = sub.add_parser("set-password", help="set the admin password for the Devices and Patients pages")
    sp.add_argument("-c", "--config", default=os.environ.get("OPENBEDSIDE_CONFIG"))
    sa = sub.add_parser("send-adt", help="send a test ADT message to the gateway (synthetic identifiers only)")
    sa.add_argument("--host", default="127.0.0.1")
    sa.add_argument("--port", type=int, default=6663)
    sa.add_argument("--event", default="A01", choices=["A01", "A02", "A03", "A04", "A08", "A11", "A12", "A13", "A40"])
    sa.add_argument("--mrn", required=True)
    sa.add_argument("--authority", default="HOSP")
    sa.add_argument("--unit", default="")
    sa.add_argument("--room", default="")
    sa.add_argument("--bed", default="")
    sa.add_argument("--visit", default="")
    sa.add_argument("--prior", default="", help="A12 only: prior location as unit^room^bed")
    sa.add_argument("--merge-from", default="", help="A40 only: the identifier being merged away")
    fp = sub.add_parser("fake-pump", help="run the fictional ACME pump used in the worked example")
    fp.add_argument("--host", default="127.0.0.1")
    fp.add_argument("--port", type=int, default=7001)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else (logging.WARNING if a.cmd == "check" else logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if a.cmd in ("run", "demo", "check"):
            cfg = load_config(a.config)
            add_adapter_paths(cfg, a.config)
            if a.cmd == "run":
                asyncio.run(run(cfg))
            elif a.cmd == "demo":
                asyncio.run(demo(cfg))
            else:
                return asyncio.run(check(cfg, a.seconds, a.show_hl7))
        elif a.cmd == "ehr-sim":
            asyncio.run(ehr_sim(a.host, a.port, a.reject_every, a.quiet))
        elif a.cmd == "send-order":
            reply = asyncio.run(mllp.send(a.host, a.port, build_test_order(a.module, a.drug, a.rate, a.vtbi)))
            m = Message(reply)
            print(f"{m.type}: {m.get('MSA', 1)} {m.get('MSA', 3)}")
        elif a.cmd == "set-password":
            import getpass
            cfg = load_config(a.config)
            reg = Registry(cfg["gateway"]["database"])
            pw = getpass.getpass("New admin password (at least 10 characters): ")
            if pw != getpass.getpass("Again: "):
                print("They did not match. Nothing changed.")
                return 1
            try:
                reg.set_admin_password(pw)
            except ValueError as ex:
                print(f"Not set: {ex}.")
                return 1
            print(f"Admin password set in {os.path.abspath(cfg['gateway']['database'])}. Restart the gateway if it is running.")
        elif a.cmd == "send-adt":
            msg = build_test_adt(a.event, a.mrn, a.unit, a.room, a.bed, a.authority, visit=a.visit,
                                 prior=a.prior, merge_from=a.merge_from)
            m = Message(asyncio.run(mllp.send(a.host, a.port, msg)))
            print(f"ADT {a.event} {a.mrn}: {m.get('MSA', 1)} {m.get('MSA', 3)}")
        elif a.cmd == "new-adapter":
            from .scaffold import make
            where = make(a.name, a.dir)
            print(f"Starter adapter written to {where}\nNext: fill in MAPPING.md, then the four sections of the adapter file.")
        elif a.cmd == "fake-pump":
            from .examples.acme_json import fake_pump
            asyncio.run(fake_pump(a.host, a.port))
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as e:
        if getattr(e, "errno", None) in (48, 98):
            print(f"\nA port is already in use: {e}. Stop the other copy, or change the port in your config.")
            return 2
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

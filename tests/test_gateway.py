import asyncio
import os
import tempfile

import pytest

from openbedside import codes
from openbedside.adapters.simulator import SimulatorAdapter
from openbedside.core.engine import Engine
from openbedside.ehr.sender import Sender
from openbedside.hl7 import mllp
from openbedside.hl7.ack import build_ack, parse_ack
from openbedside.hl7.message import Message, escape, unescape
from openbedside.hl7.pcd01 import Builder
from openbedside.model import EventKind, PumpStatus
from openbedside.orders.piv import OrderListener, build_test_order, parse_rgv
from openbedside.store.outbox import Outbox


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def outbox():
    d = tempfile.mkdtemp()
    return Outbox(os.path.join(d, "t.db"))


def sim():
    return SimulatorAdapter({"device_id": "SIM-0001", "speed": 60}, lambda d: None)


# ---- HL7 basics -------------------------------------------------------------
def test_escape_roundtrip():
    s = "a|b^c~d\\e&f"
    assert "|" not in escape(s)
    assert unescape(escape(s)) == s


def test_ack_roundtrip():
    msg = Message(Builder("GW", "F", "EHR", "H").build(sim().snapshot())[1])
    code, ctrl, _ = parse_ack(build_ack(msg, "AA"))
    assert code == "AA" and ctrl == msg.control_id


# ---- PCD-01 -----------------------------------------------------------------
def test_pcd01_structure_and_containment():
    ctrl, raw = Builder("GW", "F", "EHR", "H").build(sim().snapshot())
    m = Message(raw)
    assert m.type == "ORU^R01^ORU_R01"
    assert m.get("MSH", 21, 3) == "1.3.6.1.4.1.19376.1.6.1.1.1"
    assert m.get("MSH", 15) == "NE" and m.get("MSH", 16) == "AL"
    subs = [o[4] for o in m.all("OBX")]
    assert subs[0] == "1.0.0.0"                    # one MDS
    assert "1.1.0.0" in subs and "1.2.0.0" in subs  # two modules = two VMDs, one device
    assert "1.2.3.0" in subs                        # module B has a secondary source
    assert m.get("PID", 3) == "Unknown^^^^U"        # PID is required; unassociated = Unknown, never guessed


def test_dose_rate_withheld_without_basis():
    dev = sim().snapshot()
    src = dev.modules[0].channels[0].sources[0]
    src.concentration = None                        # remove the basis
    raw = Builder("GW", "F", "EHR", "H").build(dev)[1]
    assert codes.RATE_DOSE.refid not in raw


def test_unverified_unit_refused():
    with pytest.raises(codes.UnverifiedCode):
        codes.unit("mmHg")


# ---- simulator and derived events ------------------------------------------
def test_secondary_completes_then_primary_resumes_and_kvo():
    a = sim()
    events = []
    eng = Engine.__new__(Engine)                    # derive() only, no outbox needed
    prev = a.snapshot()
    for _ in range(200):                            # 200 simulated minutes
        a.step(60)
        cur = a.snapshot()
        events += Engine.derive(eng, prev, cur)
        prev = cur
    kinds = [e.kind for e in events]
    assert EventKind.SECONDARY_TO_PRIMARY in kinds  # vancomycin 250 mL at 166.67 mL/h finishes in 90 min
    assert EventKind.RATE_CHANGE in kinds           # heparin 12 -> 14 mL/h at minute 30
    assert all(e.derived for e in events)           # nothing claims to be a device-reported event
    b = a.mods["B"]
    assert b.secondary is None and b.status == PumpStatus.INFUSING


# ---- store and forward ------------------------------------------------------
def test_outbox_keeps_order_and_retries(outbox):
    i1 = outbox.put("d", "C1", "m1")
    outbox.put("d", "C2", "m2")
    assert outbox.head("d").id == i1
    outbox.mark_retry(i1, 0, "down")
    assert outbox.head("d") is None                 # head is waiting; C2 must not jump the queue
    assert outbox.counts()["pending"] == 2


def test_end_to_end_delivery_and_retry(outbox):
    async def scenario():
        replies = iter(["AE", "AA", "AA"])

        async def ehr(raw):
            return build_ack(Message(raw), next(replies))
        srv = await mllp.serve("127.0.0.1", 0, ehr)
        port = srv.sockets[0].getsockname()[1]
        eng = Engine(outbox, Builder("GW", "F", "EHR", "H"), "dest", periodic_seconds=0)
        eng.publish(sim().snapshot())
        s = Sender(outbox, "dest", "127.0.0.1", port)
        assert await s.once() is False              # AE: retry scheduled
        with outbox._lock:
            outbox._db.execute("UPDATE outbox SET next_attempt=0")
        assert await s.once() is True               # AA: delivered
        srv.close()
        await srv.wait_closed()
        return outbox.counts()
    counts = run(scenario())
    assert counts.get("sent") == 1


def test_sender_survives_ehr_down(outbox):
    async def scenario():
        eng = Engine(outbox, Builder("GW", "F", "EHR", "H"), "dest", periodic_seconds=0)
        eng.publish(sim().snapshot())
        s = Sender(outbox, "dest", "127.0.0.1", 1, timeout=1)   # nothing listens on port 1
        return await s.once(), s.connected
    sent, connected = run(scenario())
    assert sent is False and connected is False
    assert outbox.counts()["pending"] == 1          # still stored, waiting


# ---- orders (simulator only) ------------------------------------------------
def test_order_parse():
    req = parse_rgv(Message(build_test_order("SIM-0001-A", "heparin", 10, 100)))
    assert req.module_id == "SIM-0001-A" and req.rate_ml_h == 10 and req.vtbi_ml == 100


def test_order_rejected_while_infusing_then_accepted_when_idle():
    a = sim()
    lst = OrderListener([a])
    r1 = Message(run(lst.handle(build_test_order("SIM-0001-A", "heparin", 10, 100))))
    assert r1.type == "RRG^O16^RRG_O16" and r1.get("MSA", 1) == "AE"
    a.mods["A"].status = PumpStatus.IDLE
    r2 = Message(run(lst.handle(build_test_order("SIM-0001-A", "heparin", 10, 100))))
    assert r2.get("MSA", 1) == "AA"


def test_order_refused_for_non_programming_adapter():
    a = sim()
    a.supports_programming = False                  # stands in for any real device adapter
    r = Message(run(OrderListener([a]).handle(build_test_order("SIM-0001-A", "x", 10, 100))))
    assert r.get("MSA", 1) == "AR" and "simulator-only" in r.get("MSA", 3)

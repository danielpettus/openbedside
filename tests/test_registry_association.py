"""Registration, ADT census, patient association, and the page's access rules."""
import asyncio
import json
import os
import tempfile
import urllib.error
import urllib.request

import pytest

from openbedside.adapters.simulator import SimulatorAdapter
from openbedside.core.engine import Engine
from openbedside.ehr.adt import AdtListener, build_test_adt
from openbedside.hl7.message import Message
from openbedside.hl7.pcd01 import Builder
from openbedside.model import EventKind, PumpStatus
from openbedside.store.outbox import Outbox
from openbedside.store.registry import DeviceEntry, Registry
from openbedside.ui.server import Api, serve

V = "OpenBedside-Sim"


@pytest.fixture
def env():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    reg = Registry(path)
    out = Outbox(path)
    eng = Engine(out, Builder("GW", "F", "EHR", "H"), "dest", periodic_seconds=0, registry=reg)
    return reg, out, eng


def snap(dev_id="SIM-0001", vendor=V):
    d = SimulatorAdapter({"device_id": dev_id}, lambda d: None).snapshot()
    d.vendor = vendor
    return d


def last_msg(out):
    with out._lock:
        raw = out._db.execute("SELECT message FROM outbox ORDER BY id DESC LIMIT 1").fetchone()[0]
    return Message(raw)


def adt(reg, *args, **kw):
    return asyncio.run(AdtListener(reg).handle(build_test_adt(*args, **kw)))


def test_unregistered_device_is_held_not_sent(env):
    reg, out, eng = env
    eng.publish(snap())
    assert out.counts() == {}
    assert eng.unregistered[f"{V}|SIM-0001"]["reason"] == "not registered"
    assert reg.save_device(DeviceEntry(V, "SIM-0001")) == []
    eng.publish(snap())
    assert out.counts()["pending"] == 1 and not eng.unregistered


def test_send_policy_sends_unregistered_without_patient(env):
    reg, out, eng = env
    eng.unknown_devices = "send"
    eng.publish(snap())
    assert last_msg(out).get("PID", 3) == "Unknown^^^^U"


def test_same_serial_two_vendors_do_not_collide(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry("VendorA", "12345"))
    reg.save_device(DeviceEntry("VendorB", "12345"))
    eng.publish(snap("12345", "VendorA"))
    eng.publish(snap("12345", "VendorB"))
    assert len(eng.devices) == 2


def test_registry_validation(env):
    reg, _, _ = env
    assert reg.save_device(DeviceEntry(V, "X", ehr_id_from="asset"))       # asset tag missing
    assert reg.save_device(DeviceEntry(V, "X", eui64="12345"))             # not 16 hex
    assert reg.save_device(DeviceEntry(V, "X", assoc_adt=True))            # no bed
    assert reg.save_device(DeviceEntry(V, "X|Y"))                          # HL7 delimiter
    assert reg.save_device(DeviceEntry(V, "X", device_type="toaster"))


def test_asset_tag_and_eui64_go_to_the_ehr(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", asset_tag="BIO-004512", ehr_id_from="asset",
                                eui64="00D0750E74DA393E"))
    eng.publish(snap())
    m = last_msg(out)
    obx = m.all("OBX")
    assert obx[0][18] == "BIO-004512^OpenBedside-Sim^00D0750E74DA393E^EUI-64"
    assert obx[1][18].startswith("BIO-004512-A^")                          # module follows the asset tag


def test_ventilator_registration_holds_a_pump_data_model(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", device_type="ventilator"))
    eng.publish(snap())
    assert out.counts() == {}
    assert "ventilator" in eng.unregistered[f"{V}|SIM-0001"]["reason"]


def test_adt_location_association_and_pid_pv1(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", unit="ICU", room="101", bed="A", assoc_adt=True))
    assert Message(adt(reg, "A01", "TEST0001", "ICU", "101", "A", visit="V9")).get("MSA", 1) == "AA"
    eng.publish(snap())
    m = last_msg(out)
    assert m.get("PID", 3) == "TEST0001^^^HOSP^MR"
    assert m.get("PV1", 3) == "ICU^101^A" and m.get("PV1", 19) == "V9" and m.get("PV1", 2) == "I"
    assert eng.decisions[f"{V}|SIM-0001"].patient.source == "adt-location"


def test_discharge_clears_and_transfer_moves(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", unit="ICU", room="101", bed="A", assoc_adt=True))
    adt(reg, "A01", "TEST0001", "ICU", "101", "A")
    adt(reg, "A02", "TEST0001", "ICU", "102", "B")
    assert reg.patients_at("ICU", "101", "A") == []
    adt(reg, "A03", "TEST0001")
    assert reg.census() == []


def test_two_patients_in_one_bed_is_a_conflict(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", unit="ICU", room="101", bed="A", assoc_adt=True))
    adt(reg, "A01", "TEST0001", "ICU", "101", "A")
    adt(reg, "A01", "TEST0002", "ICU", "101", "A")
    eng.publish(snap())
    assert eng.decisions[f"{V}|SIM-0001"].state == "conflict"
    assert last_msg(out).get("PID", 3) == "Unknown^^^^U"


def test_device_reported_and_adt_disagree_withholds(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", unit="ICU", room="101", bed="A", assoc_adt=True))
    adt(reg, "A01", "TEST0001", "ICU", "101", "A")
    d = snap()
    d.reported_patient_id = "TEST0099"
    eng.publish(d)
    assert eng.decisions[f"{V}|SIM-0001"].state == "conflict"
    assert last_msg(out).get("PID", 3) == "Unknown^^^^U"
    assert any(e.kind == EventKind.ASSOCIATION_CONFLICT for e in eng.events)


def test_device_reported_and_adt_agree(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", unit="ICU", room="101", bed="A", assoc_adt=True))
    adt(reg, "A01", "TEST0001", "ICU", "101", "A")
    d = snap()
    d.reported_patient_id = "TEST0001"
    eng.publish(d)
    dec = eng.decisions[f"{V}|SIM-0001"]
    assert dec.state == "associated" and dec.patient.source == "adt-location+device"


def test_device_reported_ignored_when_registry_disallows(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", assoc_device=False))
    d = snap()
    d.reported_patient_id = "TEST0001"
    eng.publish(d)
    assert eng.decisions[f"{V}|SIM-0001"].state == "none"


def test_adt_patient_change_mid_infusion_waits_for_confirmation(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", unit="ICU", room="101", bed="A", assoc_adt=True))
    adt(reg, "A01", "TEST0001", "ICU", "101", "A")
    eng.publish(snap())                                     # simulator is infusing
    adt(reg, "A02", "TEST0001", "ICU", "102", "B")          # the patient moved; the pump did not
    adt(reg, "A01", "TEST0002", "ICU", "101", "A")          # a new patient in the old bed
    eng.reassess()
    key = f"{V}|SIM-0001"
    assert eng.decisions[key].state == "review"
    assert last_msg(out).get("PID", 3) == "Unknown^^^^U"
    api = Api(reg, lambda: {}, eng)
    assert api.association({"action": "confirm", "key": key})["ok"]
    eng.reassess()
    assert eng.decisions[key].state == "associated"
    assert last_msg(out).get("PID", 3).startswith("TEST0002^")


def test_change_accepted_when_device_idle(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001", unit="ICU", room="101", bed="A", assoc_adt=True))
    adt(reg, "A01", "TEST0001", "ICU", "101", "A")
    d = snap()
    eng.publish(d)
    adt(reg, "A03", "TEST0001")
    adt(reg, "A01", "TEST0002", "ICU", "101", "A")
    idle = snap()
    for m in idle.modules:
        for c in m.channels:
            c.status = PumpStatus.IDLE
    eng.publish(idle)
    assert eng.decisions[f"{V}|SIM-0001"].patient.patient_id == "TEST0002"


def test_merge_moves_census_and_manual(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001"))
    adt(reg, "A01", "OLD1", "ICU", "101", "A")
    reg.set_manual(f"{V}|SIM-0001", "OLD1")
    adt(reg, "A40", "NEW1", merge_from="OLD1")
    assert reg.census_find("NEW1") and not reg.census_find("OLD1")
    assert reg.get_manual(f"{V}|SIM-0001").patient_id == "NEW1"


def test_page_access_rules(env):
    reg, out, eng = env
    reg.save_device(DeviceEntry(V, "SIM-0001"))
    reg.set_manual(f"{V}|SIM-0001", "TEST123456")
    eng.publish(snap())
    srv = serve("127.0.0.1", 0, Api(reg, lambda: {"devices": [d.to_dict() for d in eng.devices.values()]}, eng))
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"

    def post(path, body, header=True, cookie=None):
        h = {"Content-Type": "application/json"}
        if header:
            h["X-OpenBedside"] = "1"
        if cookie:
            h["Cookie"] = cookie
        req = urllib.request.Request(base + path, json.dumps(body).encode(), h, method="POST")
        try:
            r = urllib.request.urlopen(req)
            return r.status, json.loads(r.read()), r.headers.get("Set-Cookie")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), None

    new = {"vendor": V, "device_id": "SIM-0002"}
    assert post("/api/registry", new, header=False)[0] == 403       # no custom header
    assert post("/api/registry", new)[0] == 200                     # no password: localhost may change
    reg.set_admin_password("correct horse battery")
    assert post("/api/registry", new)[0] == 403                     # password set: must sign in
    state = json.loads(urllib.request.urlopen(base + "/api/state").read())
    assert state["devices"][0]["association"]["patient"].endswith("3456")
    assert "TEST123456" not in json.dumps(state)                    # masked when not signed in
    code, body, cookie = post("/api/login", {"password": "correct horse battery"})
    assert code == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    tok = cookie.split(";")[0]
    assert post("/api/registry", new, cookie=tok)[0] == 200
    srv.shutdown()

#!/usr/bin/env python3
"""
http_bridge.py  ·  OpenBedside HTTP bridge for the simulated pump. 29 to 30 Sept 2026.

The same file runs two ways:
  * on your own computer as OracleListener/pump_relay.py (listens on 127.0.0.1 only), and
  * in the cloud as OpenBedside's public front door (deploy/cloud), where it is the ONLY
    port exposed. OpenBedside's own status page and its HL7 listeners stay inside the
    container on 127.0.0.1.

In the cloud:
  * GET requests are open. They return SIMULATED pump state and nothing else.
  * POST requests (bind, program) need the header X-Demo-Key to match the DEMO_KEY
    environment variable, and are rate limited per address.
  * SCENARIO keeps a bag running so the demo is live whenever someone looks.

Oracle's sandbox gives us the chart: what was ordered, given and charted. It has no
live pump, monitor or ventilator. This relay puts OpenBedside's SIMULATED pump beside
the Oracle chart, for the browser app and for the listener.

What it does, and nothing more:
  GET  /pump            the simulated pump's state, plus the same state as FHIR
                        Device and Observation resources ready for AI MedAgent
  POST /pump/associate  {"patient": "12724066"}  binds the pump to an Oracle patient id
  POST /pump/program    {"patient", "drug", "rate", "vtbi"}  sends an IHE PCD-03 order
                        to the SIMULATOR (OpenBedside refuses PCD-03 for real pumps)

Why a relay at all: the Oracle app is a web page on https://aimedagent-oracle.netlify.app.
A browser will only let that page read http://127.0.0.1 if the answer carries CORS
headers, and Chrome also asks before a public site reaches your own computer. This
adds the headers. OpenBedside itself stays unchanged.

Nothing here touches Oracle. Standard library only. Listens on 127.0.0.1 only.

  python3 pump_relay.py                          serve on 127.0.0.1:8091
  python3 http_bridge.py --host 0.0.0.0          cloud: port from $PORT, key from $DEMO_KEY
  python3 pump_relay.py --status                 print the pump once
  python3 pump_relay.py --associate 12724066     bind the pump to that patient
  python3 pump_relay.py --program "sodium chloride 0.9% 1,000 mL" --rate 125 --vtbi 1000 --patient 12724066

Not a medical device. Synthetic data only.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

OB_UI = os.environ.get("OPENBEDSIDE_UI", "http://127.0.0.1:8080")
OB_ORDERS = ("127.0.0.1", int(os.environ.get("OPENBEDSIDE_ORDERS_PORT", "6662")))
OB_KEY = "OpenBedside-Sim|SIM-0001"
MODULE = "SIM-0001-A"
AUTHORITY = "ORACLE-SANDBOX"
TAG = [{"system": "https://github.com/danielpettus/openbedside", "code": "simulated",
        "display": "Simulated device feed (OpenBedside simulator)"}]
DEMO_KEY = os.environ.get("DEMO_KEY", "")          # empty = no key needed (your own computer)
POSTS_PER_MINUTE = int(os.environ.get("POSTS_PER_MINUTE", "10"))
NOTICE = ("OpenBedside simulated pump. SIMULATED device data, synthetic patients only. "
          "Not a medical device. Not for clinical use.")


# ---- OpenBedside ------------------------------------------------------------------------

def ob_call(path: str, body: dict | None = None) -> dict:
    req = urlreq.Request(OB_UI + path, data=json.dumps(body).encode() if body is not None else None,
                         method="POST" if body is not None else "GET",
                         headers={"Content-Type": "application/json", "X-OpenBedside": "1"})
    try:
        with urlreq.urlopen(req, timeout=5) as r:
            return json.loads(r.read() or b"{}")
    except HTTPError as e:
        try:
            return json.loads(e.read() or b"{}") | {"ok": False}
        except ValueError:
            return {"ok": False, "error": f"OpenBedside answered HTTP {e.code}"}
    except (URLError, OSError) as e:
        return {"ok": False, "error": f"OpenBedside is not running on {OB_UI} ({e.__class__.__name__})"}


def fhir_devices(dev: dict, ch: dict, src: dict | None, patient: str) -> dict:
    """The pump as FHIR, for AI MedAgent's optional "devices" prefetch slot.
    Plain text codes on purpose. OpenBedside's PCD-01 carries the MDC codes; this is a
    display feed for a reasoning pass, and a code typed from memory would be worse than none."""
    when = dev.get("observed_at") or datetime.now(timezone.utc).isoformat()
    subject = {"subject": {"reference": f"Patient/{patient}"}} if patient else {}

    def obs(text, **value):
        return {"resourceType": "Observation", "meta": {"tag": TAG}, "status": "final",
                "category": [{"text": "device"}], "code": {"text": text},
                "device": {"reference": "Device/SIM-0001"}, "effectiveDateTime": when, **subject, **value}

    items = [
        {"resourceType": "Device", "id": "SIM-0001", "meta": {"tag": TAG}, "status": "active",
         "deviceName": [{"name": f"OpenBedside simulated infusion pump SIM-0001, channel {MODULE[-1]}",
                         "type": "user-friendly-name"}]},
        obs("Pump channel status", valueString=ch.get("status") or "unknown"),
        obs("Drug on the pump", valueString=(src or {}).get("drug_name") or "no program"),
        obs("Pump flow rate", valueQuantity={"value": (ch.get("actual_rate") or {}).get("value") or 0,
                                              "unit": "mL/h", "system": "http://unitsofmeasure.org", "code": "mL/h"}),
    ]
    if src and src.get("vtbi"):
        items.append(obs("Volume to be infused", valueQuantity={"value": src["vtbi"]["value"], "unit": "mL",
                                                                 "system": "http://unitsofmeasure.org", "code": "mL"}))
    if src and src.get("volume_delivered"):
        items.append(obs("Volume delivered", valueQuantity={"value": round(src["volume_delivered"]["value"], 1), "unit": "mL",
                                                             "system": "http://unitsofmeasure.org", "code": "mL"}))
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in items]}


def pump_state() -> dict:
    s = ob_call("/api/state")
    if s.get("ok") is False:
        return s
    dev = next((d for d in s.get("devices", []) if d.get("key") == OB_KEY), None)
    if not dev:
        return {"ok": False, "error": "OpenBedside is running but has no SIM-0001. Start it with openbedside-oracle.toml."}
    mod = next((m for m in dev.get("modules", []) if m.get("module_id") == MODULE), {})
    ch = (mod.get("channels") or [{}])[0]
    src = (ch.get("sources") or [None])[0]
    a = dev.get("association") or {}
    patient = a.get("patient") or ""
    out = s.get("outbox", {})
    return {
        "ok": True, "simulated": True,
        "device": {"key": OB_KEY, "module": MODULE, "online": dev.get("online"), "observed_at": dev.get("observed_at")},
        "channel": {"status": ch.get("status") or "unknown",
                    "rate_ml_h": (ch.get("actual_rate") or {}).get("value") or 0,
                    "drug": (src or {}).get("drug_name"),
                    "vtbi_ml": ((src or {}).get("vtbi") or {}).get("value"),
                    "delivered_ml": ((src or {}).get("volume_delivered") or {}).get("value")},
        "association": {"state": a.get("state"), "patient": patient, "source": a.get("source"), "detail": a.get("detail")},
        "to_ehr_pcd01": out.get("counts", {}),
        "fhir": fhir_devices(dev, ch, src, patient),
    }


def associate(patient: str) -> dict:
    patient = str(patient or "").strip()
    if not patient:
        return ob_call("/api/association", {"action": "clear", "key": OB_KEY})
    return ob_call("/api/association", {"action": "set", "key": OB_KEY, "patient_id": patient, "authority": AUTHORITY})


def mllp(msg: str) -> str:
    with socket.create_connection(OB_ORDERS, timeout=5) as s:
        s.sendall(b"\x0b" + msg.encode() + b"\x1c\r")
        buf = b""
        while b"\x1c" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    return buf.strip(b"\x0b\x1c\r").decode(errors="replace")


def program(patient: str, drug: str, rate: float, vtbi: float) -> dict:
    """IHE PIV RGV^O15, the same shape as the VistA bridge. To the simulator only."""
    if not drug or rate is None or vtbi is None:
        return {"ok": False, "error": "drug, rate and vtbi are all required"}
    rate, vtbi = float(rate), float(vtbi)
    if not (0 < rate <= 999 and 0 < vtbi <= 9999):
        return {"ok": False, "error": "rate must be 0 to 999 mL/h and vtbi 0 to 9999 mL"}
    esc = lambda t: str(t).replace("\\", "\\E\\").replace("|", "\\F\\").replace("^", "\\S\\").replace("&", "\\T\\").replace("~", "\\R\\")
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    ctrl, order_id = "OR" + now + uuid.uuid4().hex[:5], "ORACLE-DEMO-" + now
    seg = lambda *f: "|".join(str(x) for x in f)
    msg = "\r".join([
        seg("MSH", "^~\\&", "AIMEDAGENT-RELAY", "LOCAL", "OpenBedside", "GATEWAY", now, "", "RGV^O15^RGV_O15", ctrl, "P", "2.6",
            "", "", "AL", "AL", "", "", "", "", "IHE_PCD_RGV_O15^IHE PCD^1.3.6.1.4.1.19376.1.6.1.3.1^ISO"),
        seg("PID", "", "", f"{esc(patient)}^^^{AUTHORITY}^MR"),
        seg("ORC", "RE", f"{order_id}^AIMEDAGENT-RELAY"),
        seg("RXG", "1", "", "", f"^{esc(drug)}", f"{vtbi:g}", "", "263762^MDC_DIM_MILLI_L^MDC",
            "", "", "", "", "", "", "", f"{rate:g}", "265266^MDC_DIM_MILLI_L_PER_HR^MDC"),
        seg("RXR", "IV"),
        seg("OBX", "1", "", "69986^MDC_DEV_PUMP_INFUS_VMD^MDC", "1.1.0.0", "", "", "", "", "", "", "F",
            "", "", "", "", "", "", MODULE),
    ]) + "\r"
    try:
        reply = mllp(msg)
    except OSError as e:
        return {"ok": False, "error": f"OpenBedside order listener not reachable on port {OB_ORDERS[1]} ({e.__class__.__name__})"}
    msa = next((l.split("|") for l in reply.split("\r") if l.startswith("MSA")), [])
    code, text = (msa[1] if len(msa) > 1 else "?"), (msa[3] if len(msa) > 3 else reply[:200])
    return {"ok": code == "AA", "ack": code, "text": text, "order_id": order_id,
            "program": {"drug": drug, "rate_ml_h": rate, "vtbi_ml": vtbi, "module": MODULE}}


# ---- scenario: keep a bag running --------------------------------------------------------

def parse_scenario(text: str):
    """ "drug|rate|vtbi|patient"  ->  dict, or None """
    if not text:
        return None
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 4:
        raise SystemExit("SCENARIO must be: drug|rate mL/h|volume mL|patient id")
    return {"drug": parts[0], "rate": float(parts[1]), "vtbi": float(parts[2]), "patient": parts[3]}


def scenario_loop(sc: dict, every: float = 15.0) -> None:
    """Start the bag, and start it again whenever the channel is idle, complete or at KVO.
    The simulator accepts a new program in those states. A demo nobody is running should
    still be infusing when somebody opens it."""
    while True:
        s = pump_state()
        if s.get("ok") and s["channel"]["status"] in ("idle", "complete", "kvo", "unknown"):
            r = program(sc["patient"], sc["drug"], sc["rate"], sc["vtbi"])
            if r.get("ok"):
                associate(sc["patient"])
            print(f"scenario: {s['channel']['status']} -> program {r.get('ack')} {r.get('text', '')}", flush=True)
        time.sleep(every)


_posts: dict = {}
_posts_lock = threading.Lock()


def allowed(addr: str) -> bool:
    now = time.time()
    with _posts_lock:
        recent = [t for t in _posts.get(addr, []) if now - t < 60]
        if len(recent) >= POSTS_PER_MINUTE:
            _posts[addr] = recent
            return False
        recent.append(now)
        _posts[addr] = recent
        return True


# ---- server -----------------------------------------------------------------------------

def serve(port: int, host: str = "127.0.0.1") -> None:
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Demo-Key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            # Chrome's rule for a public https page reaching a private address.
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def do_OPTIONS(self):
            self._send(204, b"", "text/plain")

        def do_GET(self):
            p = urlparse(self.path).path.rstrip("/")
            if p == "/pump":
                return self._json(pump_state() | {"notice": NOTICE})
            if p == "/health":
                s = pump_state()
                return self._json({"ok": bool(s.get("ok")), "openbedside": "up" if s.get("ok") else "down"},
                                  200 if s.get("ok") else 503)
            return self._json({"notice": NOTICE,
                               "endpoints": ["GET /pump", "GET /health", "POST /pump/associate", "POST /pump/program"],
                               "writes": "need X-Demo-Key" if DEMO_KEY else "open (local mode)"},
                              200 if p == "" else 404)

        def do_POST(self):
            try:
                n = min(int(self.headers.get("Content-Length", "0")), 10000)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"ok": False, "error": "bad JSON"}, 400)
            p = urlparse(self.path).path.rstrip("/")
            if DEMO_KEY and not hmac.compare_digest(self.headers.get("X-Demo-Key", ""), DEMO_KEY):
                return self._json({"ok": False, "error": "read-only demo: this change needs the demo key"}, 403)
            client = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
            if not allowed(client):
                return self._json({"ok": False, "error": "too many changes; wait a minute"}, 429)
            try:
                if p == "/pump/associate":
                    return self._json(associate(body.get("patient", "")))
                if p == "/pump/program":
                    r = program(str(body.get("patient", "")), body.get("drug"), body.get("rate"), body.get("vtbi"))
                    if r.get("ok") and body.get("patient"):
                        r["association"] = associate(body["patient"])
                    return self._json(r)
            except (TypeError, ValueError) as e:
                return self._json({"ok": False, "error": f"bad request: {e}"}, 400)
            return self._json({"ok": False, "error": "unknown endpoint"}, 404)

        def log_message(self, *a):
            pass

    print(f"Pump relay on http://{host}:{port}/pump   (simulated pump, Ctrl-C to stop)", flush=True)
    print(f"  OpenBedside: {OB_UI}   orders on port {OB_ORDERS[1]}   writes: {'key required' if DEMO_KEY else 'open'}", flush=True)
    ThreadingHTTPServer((host, port), H).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="Relay OpenBedside's simulated pump to the Oracle experiment.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8091")))
    ap.add_argument("--host", default=os.environ.get("BIND", "127.0.0.1"),
                    help="127.0.0.1 on your computer; 0.0.0.0 in a cloud container")
    ap.add_argument("--scenario", default=os.environ.get("SCENARIO", ""),
                    help='keep a bag running: "drug|rate|vtbi|patient"')
    ap.add_argument("--status", action="store_true", help="print the pump state once and exit")
    ap.add_argument("--associate", metavar="PATIENT_ID", help="bind the pump to this patient id and exit ('' clears)")
    ap.add_argument("--program", metavar="DRUG", help="program channel A of the simulator and exit")
    ap.add_argument("--rate", type=float, help="mL/h, with --program")
    ap.add_argument("--vtbi", type=float, help="mL, with --program")
    ap.add_argument("--patient", default="", help="patient id for --program (also associates the pump)")
    a = ap.parse_args()

    if a.program:
        r = program(a.patient, a.program, a.rate, a.vtbi)
        if r.get("ok") and a.patient:
            r["association"] = associate(a.patient)
        print(json.dumps(r, indent=1)); sys.exit(0 if r.get("ok") else 1)
    if a.associate is not None:
        r = associate(a.associate); print(json.dumps(r, indent=1)); sys.exit(0 if r.get("ok", True) else 1)
    if a.status:
        s = pump_state()
        print(json.dumps({k: v for k, v in s.items() if k != "fhir"}, indent=1)); sys.exit(0 if s.get("ok") else 1)
    sc = parse_scenario(a.scenario)
    if sc:
        threading.Thread(target=scenario_loop, args=(sc,), daemon=True).start()
    if a.host not in ("127.0.0.1", "localhost") and not DEMO_KEY:
        print("WARNING: listening beyond this computer with no DEMO_KEY set. Anyone can bind or program the simulator.", flush=True)
    serve(a.port, a.host)


if __name__ == "__main__":
    main()

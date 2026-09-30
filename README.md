# OpenBedside

**An open-source gateway between bedside medical devices and the hospital's EHR.
Infusion pumps first. Built so it can be extended to ventilators and other devices.**

> **Not a medical device. Not for clinical use.** See [DISCLAIMER.md](DISCLAIMER.md).

**Get it:** [github.com/danielpettus/openbedside](https://github.com/danielpettus/openbedside).
The ready-to-use kit, with every guide as a PDF, is under
[Releases](https://github.com/danielpettus/openbedside/releases/latest).

## Why I built it

My reason is a selfish one, and I would rather say so up front.

I am building **AI MedAgent**, a medication reasoning engine
([aimedagent.net](https://aimedagent.net); the public demonstration runs on a
simulated patient). A reasoning engine is only as good as what it can see, and it
needs two feeds: the EHR and the bedside devices.

The EHR half has a door now. Federal certification rules require certified EHR
technology to offer a standardized FHIR API (ONC, 45 CFR 170.315(g)(10)). Access is
not free and not always quick, but the door exists and it looks much the same from
one EHR to the next.

The device half has no door. Every infusion pump that reports to an EHR does it
through its manufacturer's own gateway, in its own protocol, and each one is closed.
IV infusions are one of the most important feeds a medication reasoning engine can
have, because that is where the drug actually enters the patient, and they are the
hardest data to get.

So I am doing the selfish thing in the most useful way I know: opening the device
half for everyone, me included. OpenBedside is a **free, open-source device gateway.
No fees, no royalties, no permission needed.** It starts with IV infusion pumps
because that is the feed I need most, and it is built to take ventilators and other
devices next.

**OpenBedside contains no AI MedAgent code and no clinical reasoning.** It is
plumbing. It sends standard HL7 to any hospital system that wants it. AI MedAgent is
one possible consumer of that data, and not a required one.

## What it is

Every infusion pump that connects to an EHR today does it through its manufacturer's
own gateway: years of work, sold with the pump, closed, and each one different.
OpenBedside is one gateway, open to everyone. The parts are the same for every device:

- **the internal logic**: a common data model, state tracking, and events worked out
  from what the device reports;
- **store-and-forward**: every message saved to disk before it is sent, delivered in
  order, retried until the hospital acknowledges it;
- **EHR connectivity**: standard IHE Devices HL7 v2 messages over MLLP, the way
  hospital interface engines already expect them;
- **device registration and patient association**: nothing is sent until a device is
  registered, and each device's patient comes only from sources the hospital allows
  (the device itself, the ADT feed for its bed, or a person), with conflicts withheld;
- **a web page**: a live view of every device, connection, event and message, plus the
  Devices and Patients pages for registration and association.

The only part anyone writes for a new device, whether a manufacturer, a hospital
integration team or a researcher, is a **device adapter**: a short piece of Python
that translates that device's protocol into OpenBedside's data model. Every pump speaks
differently, and they all carry the same facts. The adapter is the translation.

## Open source, for anyone

Nothing here is proprietary. OpenBedside is released under the
[Apache License 2.0](LICENSE). Anyone may download it, use it, test it, modify it,
build it into their own product, and extend it to other devices. Improvements are
welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

It was designed from public sources: the IHE Devices Technical Framework, ISO/IEEE
11073 nomenclature as published, FDA guidance, and experience. No vendor's protocol
or code is in it. The worked example uses a fictional pump with an invented protocol.

## Start here

| You want to | Read |
|---|---|
| install it and see it run in 15 minutes | [START-HERE-install-on-your-Mac.md](START-HERE-install-on-your-Mac.md) (Linux is the same; Windows notes in the user guide) |
| run it, read the status page, change settings | [docs/USER-GUIDE.md](docs/USER-GUIDE.md) |
| **connect your own device** | [docs/VENDOR-INTEGRATION-GUIDE.md](docs/VENDOR-INTEGRATION-GUIDE.md) |
| know exactly what the EHR receives | [docs/HL7-INTERFACE.md](docs/HL7-INTERFACE.md) |
| understand the design | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/DECISIONS.md](docs/DECISIONS.md) |
| read it on paper or pass it on | [docs/pdf/](docs/pdf/): every guide as a PDF, and the **OpenBedside Handbook** with all of them in one file |

## The short version

```bash
python3 -m venv ~/.venvs/openbedside
source ~/.venvs/openbedside/bin/activate
pip install -e ".[dev]"          # from inside this folder
openbedside demo                 # gateway + pump simulator + stand-in EHR, one window
# then open http://127.0.0.1:8080
```

Needs Python 3.11 or newer and nothing else. No database server, no web framework,
no third-party packages at run time.

## What it does in 0.2

| Piece | Status |
|---|---|
| Data model: device, modules, channels, primary and secondary sources | working |
| Pump simulator: two modules, piggyback, rate change, KVO | working |
| Events worked out from observations: start, stop, complete, rate change, KVO, secondary to primary | working, each labelled *derived* |
| Store-and-forward (SQLite): ordered, retried, survives restarts | working |
| EHR outbound: IHE PCD-01 `ORU^R01` over MLLP, application ACK required | working |
| Web page: Activity, Devices (registration), Patients (association), sign-in | working |
| Device registry: vendor + device id, EHR identifier (serial, asset tag, EUI-64), location, allowed patient sources | working |
| ADT inbound (A01, A02, A03, A04, A08, A11, A12, A13, A40): census of identifiers and locations | working |
| Patient association: device-reported, manual, ADT by bed; conflicts withheld; mid-infusion changes need confirmation | working |
| PID always sent (`Unknown` when unassociated); PV1 with location and visit | working |
| Adapter kit: base class, worked example, checker, starter-folder generator | working |
| Orders inbound: IHE PCD-03 `RGV^O15` | **simulator only**, see the disclaimer |
| PCD-10 infusion event messages | planned |
| Ventilators | registration accepts them; data model planned (the vendor guide explains what changes) |
| Encryption (MLLP, web page, database) and access audit log | **not yet: synthetic data only** |

## Commands

```
openbedside demo                 everything in one window
openbedside run -c config.toml   the gateway
openbedside ehr-sim              a stand-in hospital interface engine
openbedside check -c config.toml check an adapter's output before a hospital sees it
openbedside new-adapter NAME     write a starter adapter folder for a device
openbedside fake-pump            the fictional ACME pump used by the worked example
openbedside send-order ...       send a test order to the simulator
openbedside send-adt ...         send a test ADT message (synthetic identifiers only)
openbedside set-password         set the admin password for the Devices and Patients pages
```

Daniel C. Pettus. Apache License 2.0.

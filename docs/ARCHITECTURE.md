# Architecture

```
  bedside devices                         OpenBedside (one process)                          hospital
 ┌──────────────┐   vendor protocol   ┌──────────────┐   internal    ┌──────────┐   PCD-01   ┌───────────────┐
 │ infusion pump│ ──────────────────► │ device       │ ────schema──► │ engine   │ ──ORU^R01─►│ interface     │
 │ ventilator   │                     │ adapter      │               │ registry │   MLLP     │ engine / EHR  │
 └──────────────┘ ◄─── (simulator     │ (one per     │               │ patient  │            └───────────────┘
                        only) ─────── │  vendor)     │               │ events   │               │         │
                                      └──────────────┘               └────┬─────┘        ADT    │         │ PCD-03
                                                                          │            A01..A40 │         │ RGV^O15
                ┌──────────────────┐                               ┌──────▼──────┐   ┌──────────▼┐  ┌─────▼─────┐
                │ web page :8080   │◄──────────────────────────────│ SQLite      │◄──│ ADT       │  │ order     │
                │ Activity         │                               │ outbox,     │   │ listener  │  │ listener  │
                │ Devices          │──── registration, manual ────►│ events,     │   └───────────┘  │ (sim only)│
                │ Patients         │     association (signed in)   │ registry,   │                  └───────────┘
                └──────────────────┘                               │ census      │
                                                                   └─────────────┘
```

## The one rule

**Vendor protocols stop at the adapter.** Everything to the right of the adapter
reads only `model.py`. That is what makes the rest identical for every manufacturer,
and it is what lets any device be connected by writing one class instead of a
gateway.

## Pieces

| Module | Job | Knows about |
|---|---|---|
| `model.py` | internal schema: Device (MDS) → Module (VMD) → Channel → Source | nothing else |
| `adapters/` | talk to devices, publish `Device` snapshots; `StreamAdapter` does the connect, read, reconnect loop | one vendor's protocol |
| `validate.py` | check snapshots before a hospital sees them (`openbedside check`) | the schema, the HL7 builder |
| `scaffold.py` | write a starter adapter folder (`openbedside new-adapter`) | nothing |
| `examples/` | the fictional ACME pump and its adapter, a complete worked integration | its own invented protocol |
| `core/engine.py` | check registration, keep state, derive events, decide what to send | the schema, the outbox, the registry |
| `core/association.py` | decide each device's patient, and say how | the registry and census |
| `store/registry.py` | registered devices, ADT census, manual associations, admin password | SQLite |
| `ehr/adt.py` | accept ADT, keep the census (identifiers and location only) | HL7 |
| `store/outbox.py` | durable queue and event log | SQLite |
| `hl7/` | build and parse HL7 v2, MLLP framing, ACKs | HL7, MDC codes |
| `ehr/sender.py` | deliver the queue, in order, on application ACK | MLLP |
| `orders/piv.py` | accept PCD-03, validate, route (simulator only) | HL7, adapters |
| `ui/server.py` | Activity, Devices and Patients pages; sign-in; JSON API | the state snapshot, the registry |

## Server and interface in one

Earlier commercial gateways split the device server from the interface engine and
ran a full SQL database server, for reasons of history as much as design. Here they
are one process, and the database is a single SQLite file next to it. A hospital,
or a developer on a laptop, installs one thing.

## Delivery guarantees

- Every outbound message is written to disk before it is sent.
- A message is delivered only when the receiver returns an application ACK (AA or
  CA) that names its control id.
- Order is kept per destination. If the oldest message cannot be delivered, nothing
  behind it goes out.
- An explicit reject (AR) is set aside for a person to review and the queue moves on.
- Everything else backs off and retries, up to five minutes between tries.

## Events: what is observed and what is inferred

IHE's infusion event profile (IPEC) requires three events: delivery start, stop and
complete. It has **no** rate change, **no** secondary-to-primary switchover and
**no** transition to KVO, which are the states a clinician needs to read a pump
record. The engine infers all of these from successive snapshots and labels every
one `derived`, so nothing downstream mistakes an inference for a device report.

## Cloud later, without redesign

The outbox is addressed by destination. A second sender can drain the same queue to
an HTTPS endpoint, a message broker or a cloud service, alongside or instead of the
EHR. Nothing in the adapters or the engine changes. Whether to do that is a market
question this project will answer when hospitals do.

## Traps the design handles on purpose

- **Rate is not dose.** A dose rate is sent only with the concentration, and weight
  when weight-based, that produced it. Missing basis means the dose rate is withheld.
- **KVO is still infusing**, at a rate unrelated to the order. It is its own status.
- **A secondary pauses the primary** on a shared line. Only the active source's rate
  is the channel's actual rate.
- **Multi-module pumps are one device.** Two modules are two VMDs under one MDS.
- **Patient identity** is never guessed. It comes from sources the registration
  allows (the device, a person, the ADT census for the device's bed), and a
  disagreement means `Unknown`. See `core/association.py`.
- **Registration before transmission.** An unregistered device is held and shown on
  the Devices page, not sent. Identity is vendor plus device id, never an IP address.
- **Clocks drift.** The model keeps both the gateway's observation time and the
  device's own clock so skew can be seen.

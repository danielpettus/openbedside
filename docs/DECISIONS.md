# Decisions

Recorded so nobody has to reconstruct why. Newest first.

## 2026-09-26: Patient association is in scope (reverses "out of scope")

Without a patient, device data is incomplete even for status: the hospital cannot
file it. So the gateway decides the patient, from authoritative sources only, and
records which: the device's own report (a wristband scanned at the pump), a manual
association on the Patients page, or the ADT census for the device's registered bed.
All allowed sources must agree or nothing is sent. Two patients in one bed is a
conflict. A patient change from ADT alone while the device is running waits for a
person, because that is the wrong-patient case: the pump went with the patient and
someone new arrived in the old bed.

Privacy: using patient identifiers to file device data is treatment, which HIPAA
permits without authorization (45 CFR 164.506). That settles whether the gateway may
use them. It does not remove the Security Rule's safeguards for whoever runs the
gateway, and a business associate agreement is needed when someone other than the
hospital operates it. This release stores identifiers and location only, no names,
and is for synthetic data until encryption and access logging are done.

## 2026-09-26: PID is always sent

DEV TF-2 Rev 10.0 (3.1.4.1.1) lists PID as required in PCD-01. 0.1 omitted it for an
unassociated device; that was wrong. An unassociated device now sends
`PID|||Unknown^^^^U||^^^^^^U`, the form used in published vendor specifications.

## 2026-09-26: Devices are registered before anything is sent

A device is known by vendor plus device id (two vendors can both ship serial
12345). Its registration says what type it is, which identifier the hospital scans
(serial, asset tag or EUI-64), where it lives, and which association sources are
allowed. Unregistered devices are held and shown, not sent, by default. IP addresses
are never identity: DHCP moves them and one device server speaks for many devices.
Registration is on the web page, so the page gained sign-in: an admin password, or
with none set, changes only from the same computer.

## 2026-09-26: Why this exists, stated plainly

The author is building AI MedAgent, a medication reasoning engine that needs EHR data
and bedside device data. Certified EHR technology must offer a standardized FHIR API
(ONC, 45 CFR 170.315(g)(10)); bedside devices have no equivalent, and IV infusion
pumps, among the most important feeds for medication reasoning, report only through
closed, manufacturer-specific gateways. OpenBedside opens that path for everyone.
IV infusion comes first for that reason. The project serves any consumer of the data
and does not depend on AI MedAgent in any way.

## 2026-09-26: Scope of 0.1: data out is real, orders are simulator only

FDA's MDDS guidance (final, 28 September 2022) treats software functions "solely
intended to transfer, store, convert formats, or display medical device data and
results" as outside device regulation, and keeps oversight of software that controls
or alters a connected device and of alarms for active patient monitoring.

So: status reporting, store-and-forward, HL7 conversion and the status page are real
in 0.1. Auto-programming (PCD-03) is built and tested end to end, but routes only to
the simulator. Alarm communication (ACM) is not in scope for the open release.

A manufacturer that takes this code into its own product can build the regulated
pieces under its own quality system. That is where they belong.

## 2026-09-26: License: Apache 2.0

The goal is an open device data path that anyone can use, so anyone must be able to
use, modify and build it into their own products, without fees or permission. Apache 2.0 does that
and adds an explicit patent grant, which covers only what the contributed code itself
practices. This project is plumbing and contains no clinical reasoning; the author's AI
reasoning work is kept out of this repository entirely.

## 2026-09-26: Clean room

This project is designed from public sources: IHE Devices Technical Framework,
ISO/IEEE 11073 nomenclature as published, FDA guidance, the author's own experience,
and the general form of publicly distributed customer-facing vendor specifications.
No text, tables or code are copied from any vendor document, and no material received
under any confidentiality agreement is used.

## 2026-09-26: One process, one file database

Server and interface engine are combined, and the store is SQLite. Earlier commercial
gateways separated them and ran a database server, largely for historical reasons.
One install is the point.

## 2026-09-26: Standard library only

No runtime dependencies. A hospital security review, or a developer on a laptop, has
one thing to evaluate and nothing to update but Python itself.

## 2026-09-26: Derived events are labelled

IHE IPEC has no rate change, no secondary-to-primary switchover and no KVO transition.
The engine infers them and marks each `derived`. A downstream system can always tell
an inference from a device report.

## 2026-09-26: MDC codes carry their provenance

No code is typed from memory. `codes.py` records where each came from and a status.
Units without a code refuse to be sent. Before production, every entry is to be
checked against the current Rosetta Terminology Mapping and marked VERIFIED.

## Open questions

- PCD-10 event codes: verify against DEV TF-2 Rev 10.0 Appendix M before building.
- Separate-connection application ACK for PCD-03, per IHE PIV.
- Location of mobile devices: association by ADT location only suits devices that
  stay put. RTLS or a bedside scan would tell the gateway where a device really is.
- PIV orders name the target module by the EHR's identifier; with asset-tag
  registration the simulator's order routing does not yet translate it back.
- Ventilator schema: DEV TF-3 Rev 10.0 section 7.2, "Device: Ventilator" (tracks the
  IEEE 11073-10303 work), and the ventilator terms in 11073-10101 as amended.
- Security: TLS for MLLP and the web page, database encryption, audit log of every
  access. Required before anyone points this at a real network or real patients.
- Name: "OpenBedside" confirmed by the author on 26 September 2026. A formal trademark search has not been done.

# HL7 interface

What the hospital's interface engine receives from OpenBedside, and what it may send.
Written for the interface analyst. HL7 v2.6, MLLP framing, UTF-8.

## Connections

| Direction | Transaction | Message | Default | Acknowledgement |
|---|---|---|---|---|
| Gateway → EHR | IHE PCD-01, Communicate PCD Data | `ORU^R01^ORU_R01` | client to `127.0.0.1:6661` | application ACK required (`MSH-15 NE`, `MSH-16 AL`) |
| EHR → Gateway | IHE PCD-03, Communicate Infusion Order | `RGV^O15^RGV_O15` | listens on `127.0.0.1:6662` | `RRG^O16` on the same connection. **Simulator only.** |
| EHR → Gateway | HL7 v2 ADT (patient location) | `ADT^A01`, `A02`, `A03`, `A04`, `A08`, `A11`, `A12`, `A13`, `A40` | listens on `127.0.0.1:6663` | `ACK`, AA for every event, used or not |
| Gateway → EHR | IHE PCD-10, Infusion Event | `ORU^R42^ORU_R01` | planned | |

Ports and hosts are set in the config file. Nothing listens on a network interface
other than localhost until someone changes that on purpose.

## When PCD-01 is sent

Only for devices registered on the Devices page (unless `[registry] unknown_devices =
"send"`), and then:

- whenever the engine derives an event on any channel of a device;
- whenever the device's patient, location or registration changes;
- otherwise every `periodic_seconds` (default 60) per device.

Each message is a full snapshot of the device, not a delta.

## Containment in OBX-4

`MDS.VMD.CHANNEL.METRIC`. For module *m*:

| OBX-4 | Meaning |
|---|---|
| `1.0.0.0` | the device (MDS). OBX-18 identifies it, see below |
| `1.m.0.0` | module *m* (VMD). OBX-18 carries the module id |
| `1.m.1.0` | delivery channel: what the module is doing now |
| `1.m.2.0` | primary source: what is hanging |
| `1.m.3.0` | secondary source, only when a piggyback is programmed |

Metrics inside a delivery channel: `.1` status, `.2` actual flow rate.
Metrics inside a source: `.1` drug name, `.2` concentration, `.3` programmed rate,
`.4` dose rate, `.5` volume to be infused, `.6` volume remaining, `.7` volume
delivered, `.8` patient weight (only when the dose rate is weight-based).

## Device identity: OBX-18

`identifier^vendor`, plus `^EUI-64 value^EUI-64` when the device's registration has
an EUI-64. The identifier is whatever the registration says the hospital scans and
files by: the serial number, the hospital asset tag, or the EUI-64. Module ids follow
it: `BIO-004512-L` for the left module of a pump registered by asset tag BIO-004512.
This follows the form in published vendor PCD-01 specifications; check it against
DEV TF-2 Appendix B before claiming conformance.

## Patient: PID and PV1

PCD-01 requires a PID segment (DEV TF-2 Rev 10.0, 3.1.4.1.1, PID `R [1..1]`), so
there is always one. The `Unknown` form below is the one used in published vendor
PCD-01 specifications; confirm it is what your interface engine expects.

| Situation | PID-3 | PV1 |
|---|---|---|
| patient associated | `id^^^authority^MR` | `PV1-2` class, `PV1-3` unit^room^bed^facility, `PV1-19` visit, from the ADT census when the patient is in it |
| no patient, device has a registered location | `Unknown^^^^U` | `PV1-2` `U`, `PV1-3` the device's registered location |
| no patient, no location | `Unknown^^^^U` | none |

PID-5 is always `^^^^^^U` (name type unspecified). The gateway does not store or send
patient names.

How the gateway decides the patient, per device, from the sources its registration
allows:

- **device**: the device reported an identifier (a wristband scanned at the pump);
- **manual**: a person associated them on the Patients page;
- **adt-location**: the ADT census has exactly one patient in the device's bed.

All allowed sources that have an answer must agree, or PID-3 is `Unknown` and the
conflict is shown on the Patients page. If ADT location is the only source and would
change the patient while the device is running, PID-3 is `Unknown` until a person
confirms. The *patient-associated*, *patient-cleared*, *association-conflict* and
*association-review* events on the activity page record every decision.

## Rules the receiver can rely on

- **The patient is never guessed.** Unknown is sent as Unknown.
- **Dose rate travels with its basis.** No concentration (or no weight for a
  weight-based dose) means no dose rate is sent.
- **Units are MDC codes with UCUM alternates** in OBX-6.
- **Timestamps carry a UTC offset.**
- **Codes are labelled.** Every MDC code used is listed in `codes.py` with its source
  and status. None is yet marked verified against the current RTM tables.

## Sample PCD-01 from the simulator

Registered to ICU 101 A with association by ADT location, after a synthetic admit of
TEST0001. First eight segments; the rest are as before.

```
MSH|^~\&|OpenBedside|GATEWAY|EHR|HOSP|20260926142209+0000||ORU^R01^ORU_R01|PD2026092614220900001|P|2.6|||NE|AL|||||IHE_PCD_ORU_R01^IHE PCD^1.3.6.1.4.1.19376.1.6.1.1.1^ISO
PID|||TEST0001^^^HOSP^MR||^^^^^^U
PV1||I|ICU^101^A||||||||||||||||V0001
OBR|1||PD2026092614220900001^OpenBedside|69985^MDC_DEV_PUMP_INFUS_MDS^MDC|||20260926142209+0000
OBX|1||69985^MDC_DEV_PUMP_INFUS_MDS^MDC|1.0.0.0|||||||X|||20260926142209+0000||||SIM-0001^OpenBedside-Sim
OBX|2||69986^MDC_DEV_PUMP_INFUS_VMD^MDC|1.1.0.0|||||||X|||20260926142209+0000||||SIM-0001-A^OpenBedside-Sim
OBX|3||126978^MDC_DEV_PUMP_INFUS_CHAN_DELIVERY^MDC|1.1.1.0|||||||X|||20260926142209+0000||||
OBX|4|CWE|184508^MDC_PUMP_STAT^MDC|1.1.1.1|^pump-status-infusing^MDC||||||R|||20260926142209+0000||||
...
```

## Sample PCD-03 accepted by the simulator

```
MSH|^~\&|TestEHR|TESTFAC|OpenBedside|GATEWAY|20260926142209+0000||RGV^O15^RGV_O15|TO2026092614220900001|P|2.6|||AL|AL|||||IHE_PCD_RGV_O15^IHE PCD^1.3.6.1.4.1.19376.1.6.1.3.1^ISO
PID|||TEST0001^^^HOSP^MR
ORC|RE|ORD0001^TestEHR
RXG|1|||^heparin|100||263762^MDC_DIM_MILLI_L^MDC||||||||10|265266^MDC_DIM_MILLI_L_PER_HR^MDC
RXR|IV
OBX|1||69986^MDC_DEV_PUMP_INFUS_VMD^MDC|1.1.0.0|||||||F|||||||SIM-0001-A
```

The target module is identified by OBX-18 on the OBX whose OBX-3 is `69986` (VMD).
The rate is read from RXG-15 with units in RXG-16 (mL/h only in this release), the
volume to be infused from RXG-5.

Reply codes in `RRG^O16` MSA-1: `AA` accepted by the simulator; `AE` could not be
done (unknown module, module infusing, parameter missing); `AR` refused (not a PIV
message, wrong version, or the target is not the simulator).

## ADT the gateway accepts

Identifiers and location only are kept: PID-3 (with PID-3.4 as the assigning
authority), PV1-2, PV1-3 and PV1-19. Names and demographics are ignored and never
stored.

| Event | Effect on the census |
|---|---|
| A01 admit, A04 register, A02 transfer, A13 cancel discharge | patient is at PV1-3 |
| A08 update | PV1-3 if present, otherwise the location already held |
| A03 discharge, A11 cancel admit | patient leaves the census |
| A12 cancel transfer | patient returns to PV1-6 (prior location) |
| A40 merge | MRG-1 becomes PID-3; manual associations follow |

Every ADT gets an `ACK` with AA, including events the gateway does not use, so the
interface engine does not hold them. A message without PID-3 gets AE.

Sample, as sent by `openbedside send-adt --mrn TEST0001 --unit ICU --room 101 --bed A --visit V0001`:

```
MSH|^~\&|TestADT|TESTFAC|OpenBedside|GATEWAY|20260926174324+0000||ADT^A01^ADT_A01|AD2026092617432400004|P|2.6
EVN|A01|20260926174324+0000
PID|1||TEST0001^^^HOSP^MR||TEST^PATIENT
PV1|1|I|ICU^101^A||||||||||||||||V0001
```

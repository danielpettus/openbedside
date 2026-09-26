# Mapping worksheet

Fill this in before writing code. Every infusion device carries the same facts in
different words. This page is where your words meet OpenBedside's. When it is
complete, the adapter is mostly copying it into two tables and one function.

Device: ______________________   Firmware / protocol version: ______________

## 1. Transport

| Question | Your answer |
|---|---|
| How does data leave the device? (serial, TCP, UDP, vendor server API, file) | |
| Who starts the connection, the device or the gateway? | |
| Message framing (one JSON per line, length prefix, HL7, binary) | |
| How often does the device report while infusing? While idle? | |
| Does it send on change, on a timer, or only when asked? | |
| What happens when the connection drops? Does it buffer and resend? | |
| Authentication or encryption on the link | |

## 2. Identity and containment

| OpenBedside field | Meaning | Your field / how derived |
|---|---|---|
| `device_id` | stable, unique per physical device (EUI-64 or serial). The hospital registers the device by vendor plus this id | |
| `vendor`, `model` | shown on the status page and in HL7; `vendor` must be the same string every time | |
| `reported_patient_id` | a patient identifier the device itself holds (a wristband scanned at the pump), or None. Never looked up or guessed by the adapter | |
| identifier the nurse scans | what is on the label the hospital scans to tie the device to a patient: serial, asset tag, EUI-64? | |
| `module_id` | stable per pump module or channel | |
| modules per device | a two-channel pump is ONE device with TWO modules | |
| `device_clock` | the device's own time, if it reports it | |

## 3. States

| Your state word | Meaning on your device | OpenBedside `PumpStatus` |
|---|---|---|
| | infusing at the programmed rate | `INFUSING` |
| | keep vein open after VTBI reached | `KVO` (still infusing) |
| | paused or on hold by a clinician | `PAUSED` |
| | stopped, no program | `IDLE` |
| | program finished | `COMPLETE` |
| | any alarm state | `ALARM` |

Anything not listed maps to `UNKNOWN`, and the checker warns about it.

## 4. Values and units

| OpenBedside field | Unit(s) allowed | Your field | Your unit string |
|---|---|---|---|
| actual rate (what the channel is doing now) | mL/h | | |
| programmed rate, per source | mL/h | | |
| VTBI | mL | | |
| volume delivered | mL | | |
| volume remaining | mL | | |
| drug name | text | | |
| concentration | mg/mL, [iU]/mL, ... | | |
| dose rate | ug/kg/min, [iU]/h, ... | | |
| patient weight | kg | | |

Rules: a number never travels without its unit; unknown is left empty; a dose rate is
only useful with the concentration (and weight, if weight based) that produced it.

## 5. Primary and secondary

| Question | Your answer |
|---|---|
| How does the device show a piggyback (secondary) is programmed? | |
| While the secondary runs, which rate does it report as "current"? | |
| Does the primary resume automatically when the secondary finishes? | |

## 6. Things OpenBedside will infer, and whether your device tells you directly

| Event | Device reports it? (message / field) |
|---|---|
| delivery start | |
| delivery stop | |
| delivery complete | |
| rate change | |
| KVO start | |
| secondary to primary | |

OpenBedside infers all of these from snapshots and labels them *derived*. If your
device reports them directly, note it: a future release can pass device-reported
events through as such.

## 7. What your device does NOT send

List anything a clinician would expect in the record that your device cannot supply
(for example: drug name, concentration, weight, alarms). Missing data is a finding,
not a detail. It goes in your integration notes to the hospital.

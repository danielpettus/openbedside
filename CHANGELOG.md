# Changelog

## Unreleased

- **Simulator scenarios from config.** An adapter block can list `modules`, each with a
  status and an optional primary and secondary infusion, and `rate_change = false` turns
  off the scripted rate change. Without `modules` the simulator behaves exactly as in 0.2.0.
- **config/vista-demo.toml.** A two-channel pump, both channels idle, in bed GEN MED B-6,
  association by manual scan only, its own database. Used by the open medication pass
  demonstration, where a phone app programs the pump with a PCD-03 order.
- **Cloud demo (deploy/cloud).** One container: gateway, simulated pump and stand-in EHR,
  with a single public port served by `http_bridge.py` (pump state as JSON and as FHIR
  Device and Observation resources). Writes need a demo key and are rate limited; a
  `SCENARIO` keeps a bag running. Includes a Dockerfile and a Render Blueprint
  (`render.yaml`). Simulated devices and synthetic data only; the host's HTTPS is the only
  encryption.
- **Cloud demo status page.** The bridge's root address serves a readable, self-refreshing page to
  browsers (pump state and recent PCD-01 messages); scripts asking for JSON still get JSON.

## 0.2.0 (26 September 2026)

- **Device registry.** Devices are registered by vendor plus device id before anything
  is sent; unregistered devices are held and listed (`[registry] unknown_devices`).
  Registration sets the device type (infusion pump, ventilator, other), the identifier
  sent to the EHR (serial, asset tag or EUI-64), location, allowed patient sources and
  an optional connection binding. Fixes a collision between two vendors' devices with
  the same serial.
- **Patient association.** From the device's own report, a manual association, or
  the ADT census for the device's bed. Sources must agree or the patient is withheld;
  two patients in one bed is a conflict; an ADT-only change while a device is running
  waits for confirmation. New events: patient-associated, patient-cleared,
  association-conflict, association-review.
- **ADT listener** on port 6663: A01, A02, A03, A04, A08, A11, A12, A13, A40.
  Identifiers and location only; names are not stored. `openbedside send-adt`.
- **PCD-01:** PID is always sent (`Unknown^^^^U` when unassociated; 0.1 omitted it,
  which DEV TF-2 does not allow). PV1 with location and visit. OBX-18 carries the
  EHR identifier and, when registered, the EUI-64.
- **Web page:** menu with Activity, Devices and Patients. Admin password
  (`openbedside set-password`), or with none set, changes from the same computer only.
  Patient identifiers masked for viewers who are not signed in.
- Adapters: `Device.reported_patient_id` replaces `patient_id`; `Device.kind`.
- Docs updated throughout; tests: 36.
- Published at github.com/danielpettus/openbedside; START-HERE gains "Step 0. Get OpenBedside".


## 0.1.0 (26 September 2026)

First packaged release, for testing with device manufacturers.

- Released under the Apache License 2.0 (LICENSE, NOTICE).
- **Adapter kit**
  - `StreamAdapter` base class: connect, read, normalize; reconnects with backoff,
    counts and skips bad messages.
  - Worked example: the fictional ACME pump (`openbedside fake-pump`) and its adapter
    (`type = "acme-example"`), with `config/acme-example.toml`.
  - `openbedside check`: runs adapters, sends nothing, reports errors, warnings and
    silences, and can print the PCD-01 a hospital would receive.
  - `openbedside new-adapter NAME`: starter folder with adapter, config, tests and the
    mapping worksheet.
  - `openbedside.validate.check_device()` for use in vendor tests.
  - `[gateway] adapter_paths` so an adapter can live in its own folder.
- `openbedside demo`: gateway, simulator and stand-in EHR in one window.
- Activity page: new **Device connections** card (connected, messages, skipped, last error).
- Clear message when a port is already in use.
- Documentation: README rewritten, USER-GUIDE, VENDOR-INTEGRATION-GUIDE,
  MAPPING-WORKSHEET, CONTRIBUTING.
- Tests: 20.
- Why it exists stated plainly in README, DECISIONS and the What It Is document: the author's
  AI MedAgent work needs device data, the EHR side has a standard API and the device side
  does not. OpenBedside contains no AI MedAgent code.
- PDFs of every guide in `docs/pdf/`, plus the combined OpenBedside Handbook and the What
  It Is document. Generated from the Markdown by `tools/build_pdfs.py`.

## 0.1.0.dev0 (26 September 2026)

First working build: data model, pump simulator, derived events, SQLite
store-and-forward, PCD-01 over MLLP with application ACK, PCD-03 to the simulator only,
read-only status page, stand-in EHR. 12 tests.

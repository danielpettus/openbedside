# Contributing

OpenBedside is open source under the Apache License 2.0. Use it, change it, build it
into your own products, extend it to other devices. Improvements that help everyone
are welcome back.

## What is most useful

- **Adapters, or pieces of them**: transport patterns, framing helpers, test harnesses.
  You do not have to publish your device's adapter; under the license you may keep it
  private. Generic pieces that would help the next manufacturer are gold.
- **Units and codes** in `codes.py`, each with the source it came from.
- **Checks** in `validate.py` for mistakes you made once and would like the next team
  not to make.
- **Ventilator support** and other device classes (see the vendor guide).
- **Verification** of the MDC codes against the current Rosetta Terminology Mapping.
- **Documentation** that would have saved you a day.

## Ground rules

1. **No proprietary material.** Nothing copied from any manufacturer's confidential
   documents, code or protocol specifications, and nothing received under a
   non-disclosure agreement. Public standards and your own work only.
2. **No code from memory.** Every MDC code carries its source. Unverified stays
   labelled unverified.
3. **Never guess clinical data.** Unknown stays empty. Units always travel with numbers.
   A dose rate always travels with its basis.
4. **Standard library only** in the core. Adapters may use whatever their device needs.
5. **Tests with every change.** `pytest` must pass.
6. **No em dashes.** House style.

## How

Until a public repository is announced, send changes or questions to the maintainer
through danielpettus.com. By contributing you agree that your contribution is licensed
under the Apache License 2.0, as the license itself provides.

## Regulatory reminder

OpenBedside is not a medical device. Contributions that would make it control a real
device (auto-programming beyond the simulator) or deliver alarms for active patient
monitoring belong in a manufacturer's own regulated product, not here.

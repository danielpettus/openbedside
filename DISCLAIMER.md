# Disclaimer

OpenBedside is engineering software for testing, learning and development.

- **It is not a medical device** and has not been cleared, approved or validated by
  the FDA or any other regulator.
- **It is not for clinical use.** Do not connect it to devices in use on patients.
- **It is not clinical decision support.** It does not check doses, rates, drug
  libraries or orders for clinical appropriateness, and nothing it displays is
  dosing guidance.
- **Auto-programming works against the built-in simulator only.** Software that
  controls or alters a connected medical device is a regulated device function
  (FDA, *Medical Device Data Systems, Medical Image Storage Devices, and Medical Image
  Communications Devices*, final guidance, 28 September 2022). This release refuses
  to route programming requests to any adapter other than the simulator.
- **Patient association is decided only from sources the hospital allows** for each
  device: an identifier the device itself reports, a person's association on the
  Patients page, or the ADT census for the device's registered bed. When they
  disagree, no patient is sent. Association by location trusts that the device is
  where it is registered; the gateway cannot see where a device really is. Getting
  the right patient remains the hospital's workflow and the hospital's responsibility.
- **Use synthetic patient data only.** Once patient identifiers flow through it, the
  gateway stores and transmits protected health information, and the HIPAA Security
  Rule's safeguards apply to whoever operates it. This release has no encryption on
  its HL7 links or web page and no encryption of its database. Real patient data
  needs all three, plus access logging, first.
- The MDC codes in `codes.py` are labelled with their source and status. None is
  marked verified against the current Rosetta Terminology Mapping yet. Verify before
  relying on any of them.

Anyone who takes this code into a regulated product takes on the regulatory
obligations for that product.

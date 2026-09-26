# Adapter reference

The step-by-step guide is [VENDOR-INTEGRATION-GUIDE.md](VENDOR-INTEGRATION-GUIDE.md).
This page is the short reference.

## What an adapter may import

These are kept stable:

| Module | What you use |
|---|---|
| `openbedside.adapters.stream` | `StreamAdapter`: subclass it, write `connect`, `read_message`, `normalize`, `close` |
| `openbedside.adapters.base` | `DeviceAdapter`, if you need full control of `run()` |
| `openbedside.model` | `Device`, `Module`, `Channel`, `InfusionSource`, `Quantity`, `PumpStatus`, `SourceRole`, `utcnow` |
| `openbedside.validate` | `check_device(dev)` for your tests; levels `ERROR`, `WARNING`, `INFO` |

## The snapshot

```
Device            device_id, vendor, model, observed_at, device_clock, patient_weight,
                  reported_patient_id (only an id the device itself holds), kind
 └─ Module        module_id                         (one per pump module or channel)
     └─ Channel   channel_id, status, actual_rate   (exactly one per module)
         └─ InfusionSource   role PRIMARY or SECONDARY, drug_name, concentration,
                             rate, dose_rate, vtbi, vtbi_remaining, volume_delivered
```

`Quantity(value, ucum)`. Supported UCUM units are the keys of `codes.UNITS`: `mL`,
`mL/h`, `mg`, `mg/mL`, `ug/kg/min`, `[iU]/mL`, `[iU]/h`, `kg`. Rates are mL/h and
volumes are mL.

`PumpStatus`: `INFUSING`, `KVO`, `PAUSED`, `IDLE`, `COMPLETE`, `ALARM`, `UNKNOWN`.

## What an adapter must not set

`Device.patient`, `ehr_id`, `eui64` and `location` are set by the gateway from the
device registry and the association step. `openbedside check` reports an ERROR if an
adapter sets `patient`. A device the hospital has not registered is held, not sent.

## Registering an adapter

```toml
[gateway]
adapter_paths = ["."]            # folders to search, relative to this config file

[[adapters]]
type = "mymodule:MyAdapter"      # module:ClassName
# any other keys are yours, available as self.config["..."]
```

## Programming

Real-device adapters do not receive programming requests in this release. Leave
`supports_programming` False and set `[orders] enabled = false`.

# OpenBedside in the cloud (demo)

One container runs the OpenBedside gateway, one simulated two-channel pump, and the
stand-in EHR. Only one port is public: `http_bridge.py`, which serves the pump's state as
JSON and as FHIR Device and Observation resources.

**Simulated devices and synthetic patients only.** OpenBedside has no encryption of its own
in this release; the host's HTTPS is the only protection on the wire. Its status page and
its HL7 listeners (6661, 6662, 8080) stay on 127.0.0.1 inside the container.

## What is public

| Request | Who | What it does |
| --- | --- | --- |
| `GET /pump` | anyone | pump state plus FHIR resources, marked SIMULATED |
| `GET /health` | anyone | is OpenBedside up |
| `POST /pump/associate` | needs `X-Demo-Key` | bind the pump to a patient id |
| `POST /pump/program` | needs `X-Demo-Key` | send an IHE PCD-03 order to the simulator |

Writes are rate limited per address (`POSTS_PER_MINUTE`, default 10).

## Settings (environment variables)

| Name | Meaning |
| --- | --- |
| `PORT` | the public port (Render uses 10000) |
| `DEMO_KEY` | required for writes. Letters and digits. Without it, writes are open, which is only acceptable on your own computer |
| `SCENARIO` | `drug|rate mL/h|volume mL|patient id`. Keeps a bag running: the bridge reprograms the channel whenever it is idle, complete or at KVO |

## Run it without Docker (what was tested)

    pip install .
    mkdir -p /data
    PORT=10000 BIND=0.0.0.0 DEMO_KEY=yourkey \
    SCENARIO="sodium chloride 0.9% 1,000 mL|125|1000|12724066" \
    python3 deploy/cloud/start.py

## Build the image

    docker build -f deploy/cloud/Dockerfile -t openbedside-demo .
    docker run -p 10000:10000 -e DEMO_KEY=yourkey \
      -e SCENARIO="sodium chloride 0.9% 1,000 mL|125|1000|12724066" openbedside-demo

## Deploy on Render

`render.yaml` at the repository root is a Render Blueprint: New, Blueprint, pick the
repository, enter a DEMO_KEY. The free plan sleeps after 15 idle minutes.

Not a medical device. Not for clinical use.

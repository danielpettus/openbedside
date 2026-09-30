#!/usr/bin/env python3
"""Start OpenBedside and its HTTP bridge in one container, and stop both if either stops.

The host (Render, Fly, a VM) restarts the container when this exits, which is what we
want: a half-running demo is worse than a restart. Standard library only."""
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = os.environ.get("OPENBEDSIDE_CONFIG", str(HERE / "cloud.toml"))


def up(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"X-OpenBedside": "1"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    print(f"OpenBedside cloud demo. Config {CONFIG}. SIMULATED devices, synthetic data only.", flush=True)
    ob = subprocess.Popen(["openbedside", "demo", "-c", CONFIG])
    for _ in range(60):
        if up("http://127.0.0.1:8080/api/state"):
            break
        if ob.poll() is not None:
            print("OpenBedside exited during start-up.", flush=True)
            return 1
        time.sleep(1)
    else:
        print("OpenBedside did not come up within 60 s.", flush=True)
        ob.terminate()
        return 1
    bridge = subprocess.Popen([sys.executable, str(HERE / "http_bridge.py")])
    procs = [ob, bridge]

    def stop(*_):
        for p in procs:
            if p.poll() is None:
                p.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while all(p.poll() is None for p in procs):
        time.sleep(2)
    stop()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    print("A process stopped; exiting so the host restarts the container.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())

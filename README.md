# mobile-edge-node

Battery-backed field telemetry node built on a repurposed Android tablet
(Samsung Galaxy Tab S7, Snapdragon 865+) running a Linux userland under Termux.
The node ingests serial data from microcontrollers over USB OTG, spools every
frame to local storage so nothing is lost when the uplink drops, and forwards
telemetry to a workstation over a Tailscale (WireGuard) mesh.

**Status: scaffold.** The design and the Android hardening guide are written.
The ingestion daemon, spooler, forwarder, and test suite are being built next.
Sections below marked *planned* describe intent, not verified behavior.

## Why a tablet

Field data collection (outdoor RF sweeps, solar flux logging, vibration
capture) forces a choice between a laptop that lasts a few hours and a bare
single-board computer that needs its own battery, regulator, radio, and case.
A 2020 flagship tablet already integrates an 8-core ARM SoC, an 8,000 mAh cell
with fuel gauging and charge protection, Wi-Fi 6, Bluetooth 5.0 LE, USB 3.2
OTG host, a display for diagnostics, and a passive-cooled aluminum body. It
costs less used than a Raspberry Pi kit with equivalent peripherals.

## Architecture

```text
[ FIELD ]   RP2040 / STM32 / analog front ends
               |  USB OTG, CDC-ACM serial (115200 to 921600 baud)
               v
[ EDGE ]    Galaxy Tab S7, Termux
            1. serial ingestion daemon           src/ingest_serial.py   (planned)
            2. SQLite WAL spool, zero-drop        src/spool.py           (planned)
            3. decimation / RMS / trigger detect  src/edge_dsp.py        (planned)
            4. forwarder over Tailscale           src/telemetry_bridge.py (planned)
               |  opportunistic, resumes after link loss
               v
[ BASE ]    workstation: base_receiver.py -> MATLAB / Python
```

See [docs/design.md](docs/design.md) for the full design and
[docs/android_hardening.md](docs/android_hardening.md) for keeping a
long-running process alive on Android.

## Quickstart (planned)

```bash
# on the tablet, inside Termux
scripts/setup_termux_env.sh
python3 src/ingest_serial.py --port /dev/ttyACM0 --baud 115200 --spool spool/field.db

# on the workstation
python3 src/base_receiver.py --listen 0.0.0.0:9000
```

## Verification (planned)

```bash
pytest tests/ -v
python3 scripts/test_loopback.py --rate 100 --duration 5
```

The loopback test runs on any machine without hardware: it generates frames,
severs the forwarder mid-stream, and checks that the spool drains in order
with no gaps and no duplicates after reconnect.

## Hardware

| Item | Detail |
| --- | --- |
| Device | Samsung Galaxy Tab S7 (2020), SM-T870 |
| SoC | Snapdragon 865+ (Kryo 585, 1x3.1 GHz + 3x2.42 GHz + 4x1.8 GHz) |
| Memory | 6 GB LPDDR4X |
| Battery | 8,000 mAh Li-Po |
| Radios | Wi-Fi 6, Bluetooth 5.0 LE, USB 3.2 Gen 1 OTG |
| Userland | Termux, Python 3, git, termux-api |

Power draw and battery runtime under continuous acquisition have not been
measured yet. Numbers will be added to `docs/benchmarks.md` when they exist.

## License

MIT. See [LICENSE](LICENSE).

# mobile-edge-node

Battery-backed field telemetry node built on a repurposed Android tablet
(Samsung Galaxy Tab S7, Snapdragon 865+) running a Linux userland under Termux.
The node ingests serial data from microcontrollers over USB OTG, spools every
frame to local storage so nothing is lost when the uplink drops, and forwards
telemetry to a workstation over a Tailscale (WireGuard) mesh.

**Status: core pipeline working, hardware bring-up next.** Framing, the
SQLite spool, the forwarder, the base receiver, and the serial ingest daemon
are implemented and covered by tests that run without hardware. Edge DSP and
the control API are not written yet. Sections marked *planned* describe
intent, not verified behavior.

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
            1. serial ingestion daemon           src/ingest_serial.py
            2. frame parser, CRC-16, resync       src/framing.py
            3. SQLite WAL spool, zero-drop        src/spool.py
            4. decimation / RMS / trigger detect  src/edge_dsp.py        (planned)
            5. forwarder over Tailscale           src/telemetry_bridge.py
               |  opportunistic, resumes after link loss
               v
[ BASE ]    workstation: base_receiver.py -> MATLAB / Python
```

See [docs/design.md](docs/design.md) for the full design and
[docs/android_hardening.md](docs/android_hardening.md) for keeping a
long-running process alive on Android.

## Quickstart

```bash
# on the tablet, inside Termux (first time only)
bash scripts/setup_termux_env.sh

# on the tablet: ingest from the microcontroller into the spool
termux-usb -l
termux-usb -r /dev/bus/usb/001/002
termux-usb -e "python3 -m src.ingest_serial --fd --spool spool/field.db" /dev/bus/usb/001/002
#   (Linux / rooted Android: python3 -m src.ingest_serial --port /dev/ttyACM0 --baud 115200)

# on the workstation: receive and write CSV
python3 -m src.base_receiver --listen 0.0.0.0:9000 --out field.csv

# on the tablet: forward the spool to the workstation over the tailnet
python3 -m src.telemetry_bridge --spool spool/field.db --host <workstation tailnet IP>
```

The microcontroller side sends the frame format in `src/framing.py`: a
2-byte sync, sequence number, microsecond timestamp, length, payload, and a
CRC-16/CCITT-FALSE. A reference RP2040 sender is *planned*.

## Verification

```bash
pip install -r requirements.txt
pytest -v
python3 scripts/test_loopback.py --rate 100 --duration 5
```

Both run on any machine with no hardware attached, and both have been run
on the tablet itself (Termux, Python 3.14, aarch64) and on macOS. The test suite covers
frame parsing under arbitrary chunking, corrupt-frame isolation, spool
durability across reopen, and an end-to-end drain while the TCP link is cut
repeatedly. The loopback script generates frames at a set rate through a
simulated serial stream with injected corruption, severs the link every
second, and passes only if every valid frame reaches the receiver exactly
once and in order. Sample run:

```text
generated      800
corrupted      8  (parser rejected 8)
valid spooled  792
received       792  duplicates dropped 0
link cuts      3
pending left   0
RESULT         PASS
```

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

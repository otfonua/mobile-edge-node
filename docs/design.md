# Design: mobile edge telemetry node

## 1. Scope

An untethered ARM Linux edge node that sits between physical instrumentation
(microcontrollers, sensors, breadboard front ends) and a workstation running
MATLAB or Python. It provides battery-backed, continuous data acquisition in
places where an AC-tethered laptop or a bare single-board computer is
impractical, and it survives uplink loss without dropping data.

## 2. Motivation

### The field instrumentation problem

Collecting sensor data outside the lab (outdoor RF antenna sweeps, solar flux
tracking, vehicle vibration logging, sensor calibration in situ) forces a
choice:

1. **Laptops.** 45 to 100 W draw, 2 to 4 hours of battery under active serial
   polling, bulky, poor in direct sun, moving parts.
2. **Single-board computers (Raspberry Pi class).** Need a separate battery
   bank, regulator, radio dongle, and enclosure, and have no display or
   power-management IC.

### What a used flagship tablet already has

- **Compute.** Snapdragon 865+: 8 Kryo 585 cores (1x3.1 GHz, 3x2.42 GHz,
  4x1.8 GHz). Decimation, RMS, and FFT for a few 100 kHz channels run
  comfortably on the ARM cores. The Hexagon DSP is not accessible from a
  Termux userland and is not part of this design.
- **Power.** 8,000 mAh Li-Po with fuel gauging, charge protection, and
  sleep states. Runtime under continuous acquisition is unmeasured. It will
  be measured and recorded in `docs/benchmarks.md`.
- **Radios.** Wi-Fi 6, Bluetooth 5.0 LE, USB 3.2 Gen 1 OTG host.
- **Form factor.** Fanless aluminum body with a display for diagnostics. Not
  ingress rated. Field use assumes a case.

## 3. Pipeline

```text
[ FIELD ]  RP2040 / STM32 / analog front ends / GPS / IMU
              |  USB OTG CDC-ACM serial, 115200 to 921600 baud; or BLE
              v
[ EDGE ]   Galaxy Tab S7, Termux
           1. serial ingestion daemon (Python, termios / pyserial)
           2. local spool: SQLite in WAL mode, every frame committed
           3. edge pre-processing: decimation, calibration, RMS, trigger detect
           4. control API: REST / WebSocket, bound to the Tailscale interface only
           5. forwarder: TCP over Tailscale (WireGuard), opportunistic
              ^                          |
              | commands / config        | telemetry
              v                          v
[ BASE ]   workstation: base_receiver.py -> MATLAB / Python
           scripts or agents drive experiments through the control API
```

### 3.1 Store and forward

Streaming straight over a phone hotspot drops frames whenever cellular
latency spikes or the tower hands off. Every incoming frame is written to a
local SQLite database in WAL mode before anything else happens. If the uplink
is gone for an hour, nothing is lost. When it returns, the forwarder drains
the backlog in chronological order and the base station acknowledges by
sequence number, so nothing is duplicated.

### 3.2 Edge pre-processing

Raw 10 kHz to 100 kHz bursts will saturate an LTE uplink. The daemon
downsamples, computes moving averages and RMS, flags threshold crossings, and
optionally computes local spectra. Summary packets stream at 5 to 10 Hz.
Full-rate raw data stays in the local spool.

### 3.3 Mesh networking

Tailscale runs as the standard Android app. The node gets a stable
end-to-end encrypted address on the tailnet, reachable from the workstation
without port forwarding, dynamic DNS, or firewall changes. The forwarder and
control API bind only to that interface.

### 3.4 Control API

A small async REST / WebSocket service on the tailnet interface lets a
base-station script (or an autonomous agent) run experiments without a human
at the tablet:

- `GET /api/v1/status`: battery state of charge, thermal headroom, active
  baud rate, unsynced frame count.
- `POST /api/v1/config`: change sampling rate, front-end gain, or trigger
  thresholds. Out-of-range values are rejected with HTTP 422.
- `POST /api/v1/dsp/analyze`: compute features (peak frequency, variance,
  anomaly windows) from the local spool and return only the result.

This makes closed-loop parameter sweeps (step a bias voltage, watch the
response, step again) a script rather than a field trip.

## 4. Operating system constraints

See [android_hardening.md](android_hardening.md). Two failure modes must be
handled: Android suspending or killing background processes, and USB
permission prompts breaking unattended reconnects.

## 5. Repository layout

```text
mobile-edge-node/
├── README.md
├── LICENSE                        MIT
├── .gitignore
├── requirements.txt               pyserial, pytest
├── docs/
│   ├── design.md                  this document
│   ├── android_hardening.md       wake lock, phantom process killer, USB OTG
│   └── benchmarks.md              power, runtime, latency (once measured)
├── src/
│   ├── framing.py                 frame format, CRC-16, streaming parser
│   ├── spool.py                   SQLite WAL spool: append, drain, ack
│   ├── protocol.py                node <-> base wire format (JSON lines, acks)
│   ├── ingest_serial.py           USB serial (pyserial or termux-usb fd) -> spool
│   ├── telemetry_bridge.py        forwarder over Tailscale with reconnect
│   ├── base_receiver.py           workstation listener, CSV sink, dedupe
│   ├── edge_dsp.py                decimation, RMS, trigger detect   (planned)
│   └── control_api.py             REST / WebSocket control plane     (planned)
├── scripts/
│   ├── setup_termux_env.sh        provision Termux
│   └── test_loopback.py           end-to-end without hardware
└── tests/
    ├── test_framing.py            chunking, resync, CRC and length corruption
    ├── test_spool.py              order, ack prefix, durability across reopen
    ├── test_bridge.py             drain across repeated link cuts, no dupes
    └── test_control_api.py        status query, config validation, 422s (planned)
```

## 6. Verification

Everything in `tests/` and `scripts/test_loopback.py` runs on a laptop with
no hardware attached. Pass criteria:

1. Severing the forwarder mid-stream leaves every frame in the spool.
2. Reconnecting drains the spool in order with zero duplicates.
3. A corrupted frame fails its checksum, is logged, and does not poison
   neighbouring frames.
4. The control API answers a status query and rejects out-of-range config
   with HTTP 422.

## 7. Applicability beyond electrical engineering

The same node serves any field discipline that puts transducers outdoors:
water-quality and stream-flow logging, structural accelerometer arrays with
local event triggering, portable pyranometer and soil-moisture surveys,
wearable IMU and EMG capture. The interface is constant:

```text
sensors --UART / I2C / BLE--> tablet (spool, pre-process) --WireGuard--> lab (MATLAB / Python / R)
```

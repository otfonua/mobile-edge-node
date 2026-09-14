#!/data/data/com.termux/files/usr/bin/bash
# Provision a fresh Termux install for mobile-edge-node.
# Run once on the tablet:  bash scripts/setup_termux_env.sh
set -euo pipefail

pkg update -y
pkg install -y python git sqlite termux-api

cd "$(dirname "$0")/.."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

mkdir -p spool

# Keep the CPU awake with the screen off. Release with termux-wake-unlock.
termux-wake-lock || echo "termux-wake-lock unavailable: install the Termux:API app"

cat <<'MSG'

Done. Next steps, all documented in docs/android_hardening.md:
  1. Settings > Apps > Termux > Battery > Unrestricted
  2. Disable the phantom process killer over adb (Android 12+)
  3. Plug in the microcontroller and run:
       termux-usb -l
       termux-usb -r <device>
       termux-usb -e "python3 -m src.ingest_serial --fd --spool spool/field.db" <device>
  4. On the workstation:  python3 -m src.base_receiver --listen 0.0.0.0:9000 --out field.csv
  5. On the tablet:       python3 -m src.telemetry_bridge --spool spool/field.db --host <workstation tailnet IP>
MSG

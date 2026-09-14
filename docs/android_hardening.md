# Keeping a headless process alive on Android

Consumer Android is built to kill anything that runs in the background. Two
mechanisms will stop an unattended telemetry daemon within minutes of the
screen turning off unless they are disabled. These steps were worked out on a
Galaxy Tab S7 running One UI and apply to most Android 12+ devices.

## 1. Doze and process reaping

**Failure mode.** With the screen off, `ActivityManager` suspends background
apps and `lowmemorykiller` reclaims their memory. Termux and its children are
gone within 10 to 15 minutes.

**Fix, three parts, all required.**

1. Hold a partial wake lock so the CPU stays up with the screen off:

   ```bash
   termux-wake-lock
   ```

   Run it once per Termux session, or at the top of the daemon's launch
   script. `termux-wake-unlock` releases it.

2. Exempt Termux from battery optimization. Settings > Apps > Termux >
   Battery > Unrestricted. On One UI also remove Termux from "Sleeping apps"
   and "Deep sleeping apps" under Battery > Background usage limits.

3. Disable the Android 12+ phantom process killer, which caps the number of
   child processes an app may spawn. This needs `adb` from a computer, or
   wireless debugging paired from the tablet itself:

   ```bash
   adb shell "settings put global settings_enable_monitor_phantom_procs false"
   adb shell "device_config set_sync_disabled_for_tests persistent"
   adb shell "device_config put activity_manager max_phantom_processes 2147483647"
   ```

   The second line stops Android from resetting the value on reboot.

## 2. USB OTG permission prompts

**Failure mode.** Android asks for permission every time a USB serial device
is plugged in. An unattended reconnect in the field never gets that tap.

**Fix.** Use the Termux API bridge instead of a raw `/dev/tty*` node:

```bash
termux-usb -l                              # list attached devices
termux-usb -r /dev/bus/usb/001/002         # request permission once
termux-usb -e "python3 src/ingest_serial.py --fd" /dev/bus/usb/001/002
```

`termux-usb -e` opens the device and hands the daemon an already-authorized
file descriptor, so the daemon never touches Android's permission flow. On a
rooted device `/dev/ttyACM0` works directly, but that is not assumed here.

## 3. Watchdog

Even with the above, the OS can still reap the process under memory pressure.
The launch script runs the daemon in a loop and restarts it on exit. Because
every frame is committed to the SQLite spool before it is acknowledged, a
restart loses nothing.

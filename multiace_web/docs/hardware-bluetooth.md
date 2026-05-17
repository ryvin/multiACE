# Hardware: BLE humidity sensors + USB Bluetooth dongle

This guide walks through wiring one or more Govee BLE humidity sensors into
the dashboard end-to-end, using the Snapmaker U1 itself as the Bluetooth host
(no separate Raspberry Pi or Home Assistant server needed).

The bridge supports **multiple Govee devices simultaneously** (e.g. one
hygrometer per ACE Pro dryer); see [Multi-device setup](#multi-device-setup)
near the end. The simpler single-device flow in the main steps is the
recommended starting point.

## Why this setup

The ACE Pro has no humidity sensor of its own (verified — `ace.py` has zero
references to humidity, the Klipper `[ace]` printer object exposes `temp` but
not RH, and Anycubic doesn't market one). The cleanest add-on is a small BLE
hygrometer placed inside the chamber that broadcasts readings to whatever's
listening. We make the U1 itself listen.

The U1's onboard Bluetooth chip (Broadcom BCM4343-class on UART) is wired in
but not brought up by PAXX firmware — the `btattach` userspace utility is
missing. Rather than patch firmware, we plug in a $10 USB BT dongle. The
kernel has `CONFIG_BT_HCIBTUSB=y` already, so a dongle auto-enumerates as
`/dev/hci0` and the existing `bluetoothd` (PID 552 by default) picks it up
over DBus. No firmware modifications.

## Bill of materials

| Part | Why | Approx cost |
|---|---|---|
| **GoveeLife H5104** (3-pack) | BLE hygrometer, Swiss-made sensor, ±3% RH, 0–60 °C operating, supported by HA's Govee BLE integration and `bleak` directly | ~$22 |
| **TP-Link UB500** USB BT 5.0 dongle | RTL8761B chipset, native Linux support via the in-kernel `btusb_rtl` driver | ~$10 |
| **Total** | | **~$32** |

Alternatives:
- Govee H5075 (single, ~$11) — same protocol family, slightly older.
- GoveeLife H5105 (e-ink display, ~$15–18) — same protocol but lower 0–50 °C operating range; you'd cap the dryer at 50 °C in `ace.cfg`.
- Generic ~$5 BT 5.0 dongles work too if they're RTL8761B / CSR-based, but the TP-Link is the cheapest "buy once, forget about it" option.

## Important: dryer temp cap

The H5104 is rated 0–60 °C operating. The ACE Pro can dry up to 70 °C
(`max_dryer_temperature: 70` in `ace.cfg`). With the H5104 inside the chamber,
**reduce `max_dryer_temperature` to 60** before running aggressive cycles:

1. Open the **Config** tab in the dashboard
2. Find `max_dryer_temperature`, change `70` → `60`
3. Click **Save & Restart**

Note that this triggers a Klipper RESTART, which interrupts an active print.
Do this between prints. The change persists in `ace.cfg`.

If you regularly dry nylon, ASA, or PC at 70 °C, use the H5105 with its
external probe, or temporarily remove the H5104 before high-temp dries.

## Step-by-step setup (when the parts arrive)

### 1. Plug in the dongle

Plug the TP-Link UB500 into any free USB port on the U1. Verify it
enumerates:

```bash
ssh root@<printer-ip>
lsusb | grep -i bluetooth
# Expected: Bus 003 Device 0XX: ID 2357:0604 TP-Link UB500 Adapter
dmesg | tail -30 | grep -iE "bluetooth|hci|btusb"
# Expected lines like: Bluetooth: hci0: RTL: examining hci_ver
ls /sys/class/bluetooth/
# Expected: hci0
```

If `hci0` shows up under `/sys/class/bluetooth/` but `/dev/hci0` is missing,
the kernel detected the dongle but couldn't load firmware. Check `dmesg` for
errors like `Direct firmware load for rtl_bt/rtl8761bu_fw.bin failed`.

PAXX firmware ships **without** the RTL Bluetooth blobs as of 2026-04, so on
a fresh dongle plug-in you'll likely need to install them yourself. The
files come from the upstream
[linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/tree/rtl_bt)
project (BSD-style redistributable license). On the printer:

```sh
# As root (the rootfs is squashfs/RO; we write to the overlay's upperdir).
mkdir -p /oem/overlay/upper/lib/firmware/rtl_bt
cd /oem/overlay/upper/lib/firmware/rtl_bt
wget https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt/rtl8761bu_fw.bin
wget https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt/rtl8761bu_config.bin

# Trigger firmware reload by rebinding the dongle (replace 4-1.3.4.4 with
# the path from `dmesg | grep "TP-Link Bluetooth"`):
echo -n "4-1.3.4.4" > /sys/bus/usb/drivers/usb/unbind
echo -n "4-1.3.4.4" > /sys/bus/usb/drivers/usb/bind
ls /dev/hci0   # should now exist
```

This is safe to run during a print: the BT dongle sits on a different USB
sub-bus from the ACE serial connections (Bus 4 vs Bus 1), so rebinding it
won't disturb the printer or ACE Pro communication. Cameras live on a
different sub-port of the same hub and are unaffected.

### 2. Pair / unpause the Govee

The H5104 doesn't pair in the traditional BLE sense — it broadcasts
unencrypted advertisements every 2 seconds. Just power it on (insert the
included AAA batteries) and place it inside the ACE Pro chamber. The radio
range is plenty for ACE-to-printer distance.

Verify the printer can see the broadcast. On PAXX firmware `hcitool` and
`bluetoothctl` are usually missing, so the most reliable approach is the
venv's `bleak`:

```bash
ssh root@<printer-ip>
/userdata/multiace-web/venv/bin/python3 - <<'PY'
import asyncio
from bleak import BleakScanner

GOVEE_MFG_IDS = (0xEC88, 0x0001)

async def main():
    print("scanning 20s for BLE devices...")
    devices = await BleakScanner.discover(timeout=20, return_adv=True)
    print("\n--- Govee candidates ---")
    for addr, (dev, adv) in devices.items():
        mfg = adv.manufacturer_data or {}
        if any(k in mfg for k in GOVEE_MFG_IDS):
            print(f"  {addr}  name={dev.name!r}  rssi={adv.rssi}")
asyncio.run(main())
PY
# Expected: one line per Govee in range, name like 'GVH5104_XXXX'
#   E8:76:C6:46:55:68  name='GVH5104_5568'  rssi=-60
#   E8:76:C4:06:69:29  name='GVH5104_6929'  rssi=-66
```

Note the MAC addresses (one per device) — you'll plug them into the bridge
config below. Stronger signal (higher rssi, closer to 0) = closer to the
printer; use that as the **primary** device.

### 3. Install the BLE bridge

The bridge is a tiny Python service that scans Govee BLE advertisements and
exposes the readings as a JSON HTTP endpoint that the multiace-web backend
already knows how to read.

The bridge ships in this repo:
- `multiace_web/tools/govee_bridge.py` — FastAPI app + bleak scan loop
- `multiace_web/tools/govee_decode.py` — pure decoder (unit-tested)
- `multiace_web/install/S64govee-bridge` — sysvinit script

`install_web.sh` already copies `tools/` into the printer's app dir and
installs the init script. After running the installer, just add `bleak`
to the venv and configure the MAC(s):

```bash
# add bleak to the venv (one-time, on the printer)
/userdata/multiace-web/venv/bin/pip install bleak

# configure (one-time): edit /userdata/multiace-web/app/.env and add:
#   GOVEE_BRIDGE_MACS=E8:76:C6:46:55:68     # one or more MACs, comma-separated
#   GOVEE_BRIDGE_PORT=7127                  # optional; default 7127

/etc/init.d/S64govee-bridge restart
```

`GOVEE_BRIDGE_MACS` is the preferred form (one or many devices). The legacy
`GOVEE_BRIDGE_MAC=A4:C1:38:XX:XX:XX` is still honored as a fallback for
older single-device installs that haven't migrated.

If neither is set, the bridge starts but never produces readings (no scan
task launched). The dashboard tile stays offline.

Verify it works (the printer typically lacks `wget` and `curl`; use the
venv's python directly):

```bash
PY=/userdata/multiace-web/venv/bin/python3
"$PY" -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:7127/sensor').read().decode())"
# Expected: {"temperature": 23.4, "humidity": 47.2, "battery": 95,
#            "rssi": -45, "name": "GVH5104_XXXX", "age_s": 1.2}

"$PY" -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:7127/health').read().decode())"
# Expected: {"ok": true, "scan_started": true, "configured": 1,
#            "devices": [{"mac": "...", "name": "GVH5104_XXXX",
#                         "have_reading": true, "age_s": 0.6}],
#            "have_reading": true, "last_error": null}
```

### 4. Wire the bridge into the dashboard

Edit `/userdata/multiace-web/app/.env`:

```
MULTIACE_HUMIDITY_URL=http://127.0.0.1:7127/sensor
MULTIACE_HUMIDITY_LABEL=ACE Pro chamber
```

Restart the web service:

```bash
/etc/init.d/S62multiace-web restart
```

Within ~5 s the dashboard's environment strip shows a new tile:

```
ACE PRO CHAMBER
47%
23.4°C ambient
```

Color-coded: <25% green (dry), 25–45% neutral, 45–60% amber (getting damp),
≥60% red (re-dry needed).

## Multi-device setup

The bridge exposes one humidity reading per configured device. Typical
deployments have one Govee per ACE Pro dryer; the dashboard's environment
strip shows the **primary** device (first MAC in `GOVEE_BRIDGE_MACS`),
while `/sensors` exposes all of them for future dashboard tiles or
external consumers.

### Config

```
# /userdata/multiace-web/app/.env
GOVEE_BRIDGE_MACS=E8:76:C6:46:55:68,E8:76:C4:06:69:29
GOVEE_BRIDGE_PORT=7127
```

- Comma- or whitespace-separated. Order matters: index 0 is the primary
  device that `/sensor` returns and the dashboard shows.
- MACs are case-insensitive; both `:` and `-` separators work.
- Duplicates are de-duplicated; unknown MACs that the bridge never sees
  appear in `/sensors` with value `null` so the UI can show "warming up".
- `GOVEE_BRIDGE_MAC=<one-mac>` is still honored as a fallback for old
  installs; it's ignored when `GOVEE_BRIDGE_MACS` is set.

### Endpoints

| Endpoint | Returns |
|---|---|
| `GET /sensor` | Primary device's reading (the format the dashboard already consumes). 503 until it's warmed up. |
| `GET /sensors` | All configured devices, keyed by normalized MAC. Pending devices appear with value `null`. |
| `GET /sensor/{key}` | One device by MAC or BLE local-name (`GVH5104_5568`, case-insensitive). 404 if unknown, 503 if not yet seen. |
| `GET /health` | `scan_started`, per-device `have_reading` + `age_s`, last bleak/dbus error. |

Example `/sensors` response with two H5104s (one per dryer):

```json
{
  "E8:76:C6:46:55:68": {
    "temperature": 27.14, "humidity": 42.7, "battery": 66,
    "rssi": -54, "name": "GVH5104_5568", "age_s": 1.2
  },
  "E8:76:C4:06:69:29": {
    "temperature": 29.44, "humidity": 36.6, "battery": 29,
    "rssi": -64, "name": "GVH5104_6929", "age_s": 0.8
  }
}
```

### Dashboard integration

The existing dashboard backend reads `MULTIACE_HUMIDITY_URL=.../sensor`
and renders a single tile. With multi-device, only the primary device is
shown by default — backwards-compatible.

To surface both readings in the UI, the multiace-web backend's humidity
adapter would need a small change to fan out to `/sensors` instead of
`/sensor` and emit one tile per device. That work is not in this guide;
file an issue if you want it prioritized.

### Battery monitoring

Each device exposes `battery` (percent) in `/sensors`. When a Govee drops
below ~30% the readings get noisy and eventually stop. Watch for
`battery < 30` in `/sensors` and swap the batteries before the device
goes dark.

## Troubleshooting

**`hci0` doesn't appear after plugging in the dongle.**
Check `dmesg | tail -50` for firmware errors. RTL chips need firmware blobs;
the U1 already has `/lib/firmware/rtlbt/` so the TP-Link should work, but
some no-name dongles ship with chips needing other firmware.

**`bleak` scan finds nothing.**
- Confirm the Govee has fresh batteries (LCD lights up).
- Confirm `bluetoothd` is running: `pgrep -af bluetoothd`. If not, start it:
  `/etc/init.d/S40bluetoothd start` (script name varies).
- Try a longer scan: `BleakScanner.discover(timeout=20)`. The Govee
  advertises every 2 s but other traffic can drown it out briefly.

**Bridge sees the device but humidity reads 0%.**
The Govee H5104's BLE encoding is documented in
[GoveeBTTempLogger](https://github.com/wcbonner/GoveeBTTempLogger) — the
manufacturer data byte layout differs slightly between H5074 / H5075 /
H5104 / H5105. The bridge handles all four; if your model isn't decoded
correctly, capture a raw advertisement with `bleak`'s `detection_callback`
and post it as an issue.

**Sensor offline tile in the dashboard.**
The `/api/print` adapter shows "sensor offline" when the upstream fetch
returns non-200 or times out. Check `wget -qO- http://127.0.0.1:7127/sensor`
directly. If the bridge is dead, restart it; if it can't see the Govee,
move the sensor closer to the printer or check batteries.

**The Govee LCD reads X but the dashboard reads Y.**
The Govee's onboard sensor sample rate is ~2 s. The bridge keeps the most
recent advertisement and serves it; the multiace-web backend caches the
upstream fetch for 30 s. Worst-case lag is ~32 s. For tighter feedback,
lower the cache TTL in `server._HUMIDITY_TTL_SEC` (test override exists).

## Future: enabling onboard BT instead of using the dongle

The U1's Broadcom combo chip is wired in (see `dmesg | grep Bluetooth`).
Bringing it up requires:

1. Cross-compiling a matching `btattach` binary from BlueZ 5.66 against the
   PAXX rootfs's glibc.
2. Dropping it into `/oem/overlay/usr/sbin/` (preserved by `/oem/.debug`).
3. Adding an `/etc/init.d/S35bt-attach` script:
   `btattach -B /dev/ttyS6 -P bcm -S 1500000 &`

Doable but fragile across PAXX firmware updates. Better as a contribution to
PAXX itself (see "Upstreaming to PAXX" in the project's design notes). The USB
dongle is the right answer for individual users today.

## Reference

- [Govee BTTempLogger — protocol decoder for H5074 / H5075 / H5104 / H5105 / etc.](https://github.com/wcbonner/GoveeBTTempLogger)
- [Bleak — Python BLE library, talks to BlueZ over DBus on Linux](https://github.com/hbldh/bleak)
- [Home Assistant Govee BLE integration](https://www.home-assistant.io/integrations/govee_ble/)
- [TP-Link UB500 (RTL8761B chipset)](https://www.amazon.com/TP-Link-UB500-Bluetooth-Compatibility-Receiver/dp/B09DPL3X62)
- [GoveeLife H5104 3-pack](https://www.amazon.com/GoveeLife-Hygrometer-Thermometer-Bluetooth-Temperature/dp/B0CGRDQ2WB)

# Copyright (C) 2026  multiACE contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keepalive helper for cached non-active ACE Pro serials.

The ACE Pro firmware resets its USB interface every ~3-5s when it sees no
host traffic. The kernel re-enumerates the device (often with a different
ttyACM name); the original `serial.Serial` handle's `is_open` attribute
keeps returning True (it only reflects an explicit `.close()`), so a naive
keepalive loop happily re-uses a dead descriptor forever, raising
`OSError errno 5 (EIO)` on every write.

`attempt_keepalive` is the per-tick action: it sends the frame on the
cached serial, drains the input buffer, and on any write/read failure it
closes the stale handle and reopens by-path via an injectable factory.
Returning the (handle, connected) tuple to the caller keeps this module
free of multiACE state — the caller swaps the new handle into its cache.

Pulled out of ace.py specifically so it can be unit-tested without the
Klipper framework or pyserial in scope.
"""

try:
    import serial as _serial  # noqa: F401
except ImportError:
    _serial = None  # tests can pass an explicit serial_factory


def _default_serial_factory(path, baud):
    """Match the parameters used by _open_inactive_serials in ace.py."""
    if _serial is None:
        raise RuntimeError("pyserial not available — pass serial_factory")
    return _serial.Serial(
        port=path,
        baudrate=baud,
        exclusive=True,
        rtscts=True,
        timeout=0,
        write_timeout=0,
    )


def _try_write_and_drain(ser, frame):
    """Send frame, then drain whatever the ACE buffered. Raises on EIO."""
    ser.write(frame)
    n = ser.in_waiting
    if n:
        ser.read(n)


def _safe_close(ser):
    try:
        ser.close()
    except Exception:
        pass


def attempt_keepalive(*, idx, ser, path, baud, frame, usb_log,
                      serial_factory=None):
    """Send a keepalive frame to one ACE. Reopen the serial on failure.

    Returns (new_ser, connected). On clean keepalive new_ser is ser; on
    reopen, new_ser is the fresh handle; on terminal failure (None, False)
    and caller pops cache.
    """
    factory = serial_factory or _default_serial_factory

    if ser is not None and getattr(ser, 'is_open', False):
        try:
            _try_write_and_drain(ser, frame)
            return ser, True
        except Exception as e:
            usb_log.warning(
                'KEEPALIVE idx=%d failed: %s — reopening', idx, e)
            _safe_close(ser)

    if path is None:
        return None, False
    try:
        new_ser = factory(path, baud)
    except Exception as e:
        usb_log.warning(
            'KEEPALIVE_REOPEN idx=%d serial=%s failed: %s', idx, path, e)
        return None, False
    if new_ser is None or not getattr(new_ser, 'is_open', False):
        usb_log.warning(
            'KEEPALIVE_REOPEN idx=%d serial=%s open returned closed',
            idx, path)
        return None, False

    try:
        _try_write_and_drain(new_ser, frame)
    except Exception as e:
        usb_log.warning(
            'KEEPALIVE_REOPEN idx=%d serial=%s post-open write failed: %s',
            idx, path, e)
        _safe_close(new_ser)
        return None, False

    usb_log.info(
        'KEEPALIVE_REOPEN idx=%d serial=%s ok', idx, path)
    return new_ser, True

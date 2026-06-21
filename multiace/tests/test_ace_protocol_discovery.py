"""Tests for USB device discovery in the ACE protocol layer.

Covers AceProtocol._read_usb_ids (the sysfs-walk that resolves vid:pid for a
tty device) and its use in the V1/V2 discover paths. The walk matters because
idVendor/idProduct live on the USB *device* node, which is ONE level up from
the tty for CDC-ACM (ttyACM, the genuine ACE Pro cable) but TWO levels up for
usb-serial adapters (ttyUSB: CH340/FTDI insert an extra port node). The ACE 2
(vendor 1a86:55d3) is a CH340 and enumerates as ttyUSB, so a fixed
'device/..' lookup silently dropped it before the vid:pid check ran.

The V1/V2 modules use relative imports (`from .ace_protocol import ...`), so we
load them under a synthetic package to resolve those imports outside Klipper.
"""
import importlib
import os
import sys
import types

import pytest

_EXTRAS = os.path.join(os.path.dirname(__file__), "..", "klipper", "extras")

# Register a synthetic package so `from .ace_protocol import AceProtocol`
# resolves when loading the V1/V2 modules outside Klipper's package context.
_PKG = "maceextras"
if _PKG not in sys.modules:
    _pkg_mod = types.ModuleType(_PKG)
    _pkg_mod.__path__ = [os.path.abspath(_EXTRAS)]
    sys.modules[_PKG] = _pkg_mod

ace_protocol = importlib.import_module("%s.ace_protocol" % _PKG)
ace_protocol_v1 = importlib.import_module("%s.ace_protocol_v1" % _PKG)
ace_protocol_v2 = importlib.import_module("%s.ace_protocol_v2" % _PKG)

AceProtocol = ace_protocol.AceProtocol


def _make_device_node(tmp_path, vendor, product):
    """Create a fake USB device sysfs node holding idVendor/idProduct."""
    dev = tmp_path / "usbdev"
    dev.mkdir()
    (dev / "idVendor").write_text(vendor + "\n")
    (dev / "idProduct").write_text(product + "\n")
    return dev


# --- _read_usb_ids ---------------------------------------------------------

def test_read_usb_ids_cdc_acm_ids_one_level_up(tmp_path, monkeypatch):
    # Arrange: genuine ACE Pro cable (ttyACM) — ids on the node itself.
    dev = _make_device_node(tmp_path, "28E9", "018A")
    monkeypatch.setattr(ace_protocol.os.path, "realpath", lambda p: str(dev))

    # Act
    vendor, product = AceProtocol._read_usb_ids("ttyACM0")

    # Assert: returned lowercase-hex.
    assert (vendor, product) == ("28e9", "018a")


def test_read_usb_ids_serial_adapter_ids_two_levels_up(tmp_path, monkeypatch):
    # Arrange: usb-serial adapter (ttyUSB, e.g. ACE 2 CH340) — an extra port
    # node sits between the tty and the USB device, so ids are TWO levels up.
    dev = _make_device_node(tmp_path, "1a86", "55d3")
    port = dev / "port"
    port.mkdir()
    monkeypatch.setattr(ace_protocol.os.path, "realpath", lambda p: str(port))

    # Act
    vendor, product = AceProtocol._read_usb_ids("ttyUSB0")

    # Assert: the walk climbs to the device node and still resolves the ids.
    assert (vendor, product) == ("1a86", "55d3")


def test_read_usb_ids_returns_none_when_not_found(tmp_path, monkeypatch):
    # Arrange: a node chain with no idVendor/idProduct anywhere.
    base = tmp_path / "noids"
    base.mkdir()
    monkeypatch.setattr(ace_protocol.os.path, "realpath", lambda p: str(base))

    # Act / Assert
    assert AceProtocol._read_usb_ids("ttyUSB9") == (None, None)


def test_read_usb_ids_returns_none_on_realpath_oserror(monkeypatch):
    # Arrange: realpath itself fails (device vanished mid-enumeration).
    def boom(_p):
        raise OSError("no such device")

    monkeypatch.setattr(ace_protocol.os.path, "realpath", boom)

    # Act / Assert
    assert AceProtocol._read_usb_ids("ttyACM3") == (None, None)


# --- V1 discover wiring ----------------------------------------------------

def _patch_by_path(monkeypatch, module, entries, real_dev):
    """Make <module>.discover see a single by-path entry resolving to real_dev."""
    monkeypatch.setattr(module.os.path, "exists", lambda p: True)
    monkeypatch.setattr(module.os, "listdir", lambda d: entries)
    monkeypatch.setattr(module.os.path, "realpath",
                        lambda p: "/dev/%s" % real_dev)


def test_v1_discover_matches_via_read_usb_ids(monkeypatch):
    # Arrange: one by-path entry whose vid:pid matches the V1 ACE.
    _patch_by_path(monkeypatch, ace_protocol_v1, ["usb-ace-port0"], "ttyACM0")
    monkeypatch.setattr(ace_protocol_v1.AceProtocolV1, "_read_usb_ids",
                        classmethod(lambda cls, dev: ("28e9", "018a")))

    # Act
    found = ace_protocol_v1.AceProtocolV1.discover()

    # Assert
    assert found == ["/dev/serial/by-path/usb-ace-port0"]


def test_v1_discover_skips_non_matching_ids(monkeypatch):
    # Arrange: a device whose ids are not the V1 ACE.
    _patch_by_path(monkeypatch, ace_protocol_v1, ["usb-other-port0"], "ttyUSB0")
    monkeypatch.setattr(ace_protocol_v1.AceProtocolV1, "_read_usb_ids",
                        classmethod(lambda cls, dev: ("0403", "6001")))

    # Act / Assert
    assert ace_protocol_v1.AceProtocolV1.discover() == []


# --- V2 discover wiring (the latent ACE 2 bug) -----------------------------

def test_v2_scan_finds_ch340_ttyusb_device(monkeypatch):
    # Arrange: the ACE 2 is a CH340 (1a86:55d3) on ttyUSB — two levels up.
    _patch_by_path(monkeypatch, ace_protocol_v2, ["usb-ace2-port0"], "ttyUSB0")
    monkeypatch.setattr(ace_protocol_v2.AceProtocolV2, "_read_usb_ids",
                        classmethod(lambda cls, dev: ("1a86", "55d3")))

    # Act
    found = ace_protocol_v2.AceProtocolV2._scan_v2_serial_paths()

    # Assert: the ttyUSB-mounted ACE 2 is now discovered.
    assert found == ["/dev/serial/by-path/usb-ace2-port0"]

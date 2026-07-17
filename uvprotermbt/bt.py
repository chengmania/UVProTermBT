"""Bluetooth device discovery via BlueZ D-Bus, for the setup wizard / Radio
menu. Kept separate from the link so the UI can enumerate radios without
opening a KISS connection. Import-guarded like link.py so the package still
loads without the D-Bus bindings.
"""

from __future__ import annotations

from dataclasses import dataclass

try:  # pragma: no cover - availability depends on the host
    import dbus

    _DBUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DBUS_AVAILABLE = False

_BLUEZ = "org.bluez"
_ADAPTER_IFACE = "org.bluez.Adapter1"
_DEVICE_IFACE = "org.bluez.Device1"
# Heuristic: names of the radios this app targets (Benshi family).
_RADIO_HINTS = ("UV-PRO", "UVPRO", "VR-N", "GA-5WB", "GMRS-PRO", "BENSHI")


@dataclass
class BtDevice:
    mac: str
    name: str
    paired: bool

    @property
    def looks_like_radio(self) -> bool:
        n = self.name.upper()
        return any(h in n for h in _RADIO_HINTS)

    def label(self) -> str:
        tag = "  ✓ paired" if self.paired else ""
        return f"{self.name or '(unknown)'}  [{self.mac}]{tag}"


def available() -> bool:
    return _DBUS_AVAILABLE


def list_devices() -> list[BtDevice]:
    """Return BlueZ-known devices (paired + previously seen) without starting a
    scan — fast and non-blocking. Radios sort first."""
    if not _DBUS_AVAILABLE:
        return []
    bus = dbus.SystemBus()
    mgr = dbus.Interface(bus.get_object(_BLUEZ, "/"),
                         "org.freedesktop.DBus.ObjectManager")
    out: list[BtDevice] = []
    for _path, ifaces in mgr.GetManagedObjects().items():
        dev = ifaces.get(_DEVICE_IFACE)
        if not dev:
            continue
        out.append(BtDevice(
            mac=str(dev.get("Address", "")),
            name=str(dev.get("Name", dev.get("Alias", ""))),
            paired=bool(dev.get("Paired", False)),
        ))
    out.sort(key=lambda d: (not d.looks_like_radio, not d.paired, d.name.lower()))
    return out


def _adapter_path(bus) -> str | None:
    mgr = dbus.Interface(bus.get_object(_BLUEZ, "/"),
                         "org.freedesktop.DBus.ObjectManager")
    for path, ifaces in mgr.GetManagedObjects().items():
        if _ADAPTER_IFACE in ifaces:
            return path
    return None


def set_discovery(on: bool) -> None:
    """Start/stop BlueZ discovery so newly-powered radios show up. Call
    set_discovery(True), wait a few seconds, list_devices(), then
    set_discovery(False). Best-effort; ignores 'already (not) discovering'."""
    if not _DBUS_AVAILABLE:
        return
    bus = dbus.SystemBus()
    path = _adapter_path(bus)
    if path is None:
        return
    adapter = dbus.Interface(bus.get_object(_BLUEZ, path), _ADAPTER_IFACE)
    try:
        adapter.StartDiscovery() if on else adapter.StopDiscovery()
    except dbus.DBusException:
        pass

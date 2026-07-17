"""Persistent app settings (JSON in the user config dir).

Plain JSON so it can be hand-edited if ever needed, but the app edits it too
(Settings screen, Phase 7). Kept dependency-free and importable without the
UI or Bluetooth layers.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "uvprotermbt" / "config.json"


# Neutral out-of-the-box defaults: a fresh install must NOT transmit as someone
# else's callsign or try to reach a hard-coded radio. The first-run wizard
# collects the real values; is_configured() gates transmit.
DEFAULT_CALLSIGN = "N0CALL"


@dataclass
class Settings:
    callsign: str = DEFAULT_CALLSIGN
    ssid: int = 0
    bt_mac: str = ""  # user's radio MAC — picked in the first-run wizard
    aprs_path: str = "WIDE1-1,WIDE2-1"
    theme: str = "dark"  # "dark" | "light"
    beacon: bool = False
    winlink_call: str = ""  # Winlink often uses a distinct SSID (e.g. -10)

    @property
    def mycall(self) -> str:
        return f"{self.callsign}-{self.ssid}"

    @property
    def winlink_callsign(self) -> str:
        """Callsign used for the Winlink AX.25 port. Defaults to mycall; set
        winlink_call to use a different SSID (Winlink users commonly use -10)."""
        return self.winlink_call.strip().upper() or self.mycall

    def is_configured(self) -> bool:
        """True once the user has set a real callsign and a radio MAC. Until
        then the app must not transmit (Part 97: no TX without your callsign)."""
        return (self.callsign not in ("", DEFAULT_CALLSIGN)
                and bool(self.bt_mac.strip()))

    def path_list(self) -> list[tuple[str, int]]:
        """Parse aprs_path 'WIDE1-1,WIDE2-1' into [(call, ssid), ...]."""
        out: list[tuple[str, int]] = []
        for hop in self.aprs_path.split(","):
            hop = hop.strip()
            if not hop:
                continue
            call, _, ssid = hop.partition("-")
            out.append((call.upper(), int(ssid or 0)))
        return out

    # ---- persistence ----------------------------------------------------

    @classmethod
    def load(cls) -> "Settings":
        path = _config_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        known = {f for f in cls().__dict__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

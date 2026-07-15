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


@dataclass
class Settings:
    callsign: str = "KC3SMW"
    ssid: int = 7  # -7 handheld convention
    bt_mac: str = "38:D2:00:01:38:8F"
    aprs_path: str = "WIDE1-1,WIDE2-1"
    theme: str = "dark"  # "dark" | "light"
    beacon: bool = False

    @property
    def mycall(self) -> str:
        return f"{self.callsign}-{self.ssid}"

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

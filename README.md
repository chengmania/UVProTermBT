# UVProTermBT

A desktop **AX.25 packet messenger + terminal** for the **BTech UV-Pro** /
**VGC VR-N76** (the "Benshi" radio family), talking to the radio's built-in
**KISS TNC over classic Bluetooth** — no cable, no external TNC.

Four modes in one PyQt6 window (styled after OpenWave), light/dark themes:

- **Chat** — SMS-style APRS messaging (send/receive, auto-ack).
- **APRS** — live monitor of decoded traffic + a heard-stations list.
- **BBS** — a full AX.25 connected-mode terminal (connect to BPQ/nodes).
- **Winlink** — connects at the AX.25 layer (Winlink B2F protocol is WIP).

> **Why this exists:** the usual Linux recipe for a Bluetooth KISS TNC
> (`kissattach /dev/rfcomm0`) does **not** work with this radio. Its KISS
> session is only served through BlueZ's **SerialPort profile**, reached via an
> SDP-negotiated connection — so UVProTermBT registers an `org.bluez.Profile1`
> and reads the RFCOMM fd BlueZ hands back. See
> [`docs/UVPRO_N76_KISS_LINUX.md`](docs/UVPRO_N76_KISS_LINUX.md) and
> [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the full story.

## Requirements

- Linux with BlueZ (developed on Kubuntu), Python 3.12+
- A BTech UV-Pro / VGC VR-N76 with **KISS TNC** firmware
- System BlueZ D-Bus bindings: `python3-dbus`, `python3-gi`

## Install & run

```bash
git clone https://github.com/chengmania/UVProTermBT
cd UVProTermBT
./run.sh
```

That's it. **`./run.sh`** installs everything it needs the first time (asks for
your password once, for `apt`), then launches the app. Every run after that it
just starts. After the first run there's also a **"UVProTermBT" icon in your
app menu** you can use instead.

<details><summary>Prefer to do it by hand?</summary>

```bash
sudo apt install python3-dbus python3-gi
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvprotermbt
```
</details>

First launch runs a **setup wizard**: enter your callsign, then pick your
radio. On the radio, first **enable KISS TNC** (Settings → General Settings →
KISS TNC → Enable KISS TNC) and **pair** it to this computer.

## Using it

- Status bar shows your callsign, Bluetooth state (● green = connected), and
  the BBS session.
- **Chat:** `/to CALL` sets who messages go to, then type and press Enter.
- **BBS:** `/connect KC3SMW-7` (direct) or `/connect NODE via DIGI`; type
  commands to the node; `/ex` and other node commands pass through; `/bye` to
  disconnect.
- **Up/Down** recalls previous input; double-click a heard station to set it as
  your chat target; **Ctrl-T** toggles the theme.

## Troubleshooting

If pairing or connecting misbehaves, it's usually a stale Bluetooth bond — see
the fix in [`docs/UVPRO_N76_KISS_LINUX.md`](docs/UVPRO_N76_KISS_LINUX.md).
Connected-mode BBS work is happiest on a quiet frequency; keep the radio's own
APRS beacon off while using it as a TNC.

## License / credit

By Greg, **KC3SMW**. Built on findings from the sibling
[AXTermPuter](https://github.com/chengmania/AXTermPuter) project and the
`khusmann/benlink` radio reverse-engineering work.

Copyright (C) 2026 Greg (KC3SMW).

UVProTermBT is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License v3.0** as published by the Free Software
Foundation. This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the [`LICENSE`](LICENSE) file for the full
license text, or <https://www.gnu.org/licenses/gpl-3.0.html>.

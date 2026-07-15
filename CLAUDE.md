# UVProTermBT

Desktop/laptop AX.25 packet messenger + terminal, linking over classic
Bluetooth (SPP/RFCOMM) to a BTech UV-Pro (KISS TNC mode) for RF. Sibling
project to AXTermPuter (~/Dev/Projects/AxTermPuter) — same feature scope,
different host platform. AXTermPuter targets the M5Stack Cardputer, but
its ESP32-S3 has no classic Bluetooth radio and the UV-Pro's real KISS/TNC
session only flows over classic Bluetooth RFCOMM (see
AxTermPuter/docs/PROTOCOL.md §2 for the full findings) — so this project
runs on hardware that actually has a classic BT radio: a laptop or tablet.

Two personalities, one app, mirroring AXTermPuter's scope:

- **APRS Mode** — SMS-style APRS messaging (send / receive / ack),
  heard-station list, 67-char message limit, retry with backoff.
- **Terminal Mode** — AX.25 connected-mode client (a "packet terminal") for
  connecting to BBS/nodes, primarily ChengmaniaBPQ (KC3SMW BPQ32 node).

Author/owner: Greg — **chengmania** (KC3SMW). Use `chengmania` in all author
fields, headers, and docs.

## Repository

- GitHub: `github.com/chengmania/UVProTermBT`
- Local dev: `~/Dev/Projects/UVProTermBT` (LinuxRyzen, Kubuntu)

## Target Hardware / Platforms

- **Dev machine:** LinuxRyzen (Kubuntu) — primary development and testing.
- **Field devices:** Chuwi MiniBook laptop, HiMax10 tablet — both Kubuntu.
  All daily-use devices are Linux; there is no Windows hardware to test
  against day to day.
- **Radio:** BTech UV-Pro via classic Bluetooth (BR/EDR), SPP/RFCOMM, KISS
  TNC mode. Confirmed via live HCI capture (see docs/PROTOCOL.md) that the
  radio's real KISS data flows over classic RFCOMM, not BLE GATT — do not
  attempt a BLE approach here, it's a dead end (see AXTermPuter's findings).
- **Stretch goal:** package a Windows executable too via GitHub
  Actions/PyInstaller for others to use, even though it isn't part of the
  primary dev loop. Don't let Windows compatibility block Linux progress.

## Tech Stack

- Python 3.12+
- Classic Bluetooth RFCOMM via stdlib `socket.AF_BLUETOOTH` /
  `socket.BTPROTO_RFCOMM` (Linux-native, no extra dependency) — this is
  the whole point of the pivot from AXTermPuter: laptops/tablets have a
  real classic-BT radio, so we can talk directly to the UV-Pro's actual
  KISS transport instead of routing around it.
- `urwid` for the terminal UI (status bar, scrollback, input line, mode
  switching) — chosen for good split-pane/scrollback support across
  terminal environments.
- KISS/AX.25/APRS logic lives in `uvprotermbt/` as plain, dependency-free
  Python modules (`kiss.py`, `ax25.py`, `aprs.py`, `term.py`) so they're
  easily unit-tested with `pytest`, independent of the UI layer or the
  Bluetooth transport.
- `pytest` for tests.
- PyInstaller + a GitHub Actions release workflow build standalone
  executables for Linux (primary) and Windows (stretch) from tagged
  releases.

## Architecture (layered, bottom-up)

1. `RadioLink` (interface, same concept as AXTermPuter) → `Rfcomm KissLink`
   (classic BT RFCOMM socket to the UV-Pro) — the transport is swappable
   in principle, but classic RFCOMM is the only one that matters here.
2. `kiss.py` — KISS framing/unframing (FEND/FESC escaping, port 0 data) —
   ported directly from AXTermPuter's native-tested `lib/kiss/kiss_codec`.
3. `ax25.py` — frame encode/decode (UI frames for APRS; SABM/UA/I/RR/DISC
   state machine for connected mode)
4. `aprs.py` — message encode/decode, ack/retry queue, heard-station table
5. `term.py` — connected-mode session, line buffer, scrollback
6. `ui.py` (urwid) — mode switcher, screens, keyboard handling, status bar
   (BT state, mode, my call)

## Configuration

- Callsign default `KC3SMW`, SSID selectable (suggest -7 for handheld
  convention, -10/-15 seen in on-air testing so far — see docs/PROTOCOL.md)
- Settings persisted to a local config file (JSON, user config dir):
  callsign, SSID, APRS path (default `WIDE1-1,WIDE2-1`), BT target MAC,
  RFCOMM channel, beacon on/off
- Config editable from within the app; no hand-editing required for normal
  use, but the file is plain JSON if manual tweaks are ever needed

## Conventions

- Python 3.12+, 4-space indent, type hints on public functions,
  `snake_case` everywhere (modules, functions, variables), `PascalCase`
  classes
- Every session: append a dated entry to `LOG.txt` (what changed, what's
  next) — same discipline as AXTermPuter
- Keep `ROADMAP.md` phase checkboxes current
- Protocol details and UV-Pro classic-BT specifics live in
  `docs/PROTOCOL.md` — update it when reverse-engineering findings change
  (this carries over the RFCOMM channel/KISS findings from AXTermPuter's
  BLE investigation, plus anything new found running natively here)
- Commit style: `phaseN: short description` (e.g. `phase1: KISS codec`)

## Build & Test

```bash
# --system-site-packages is REQUIRED: the BT transport uses the system
# BlueZ D-Bus bindings (python3-dbus, python3-gi). See docs/PROTOCOL.md §3.
sudo apt install python3-dbus python3-gi        # once, system-wide
python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/            # unit tests
.venv/bin/python -m uvprotermbt               # run the app
```

## Ground Rules

- Amateur radio legal: no encryption of message content over RF; callsign
  in every transmitted frame
- Protocol code (KISS/AX.25/APRS) must have unit tests before being wired
  to the live Bluetooth transport
- Don't block the UI event loop — Bluetooth RX runs on its own thread,
  feeding a queue the UI polls (mirrors AXTermPuter's FreeRTOS-task rule,
  same principle, different concurrency primitive)
- Terminal UI should degrade gracefully to whatever terminal size is
  available (Chuwi MiniBook and HiMax10 have small/unusual screens) —
  don't assume a large terminal

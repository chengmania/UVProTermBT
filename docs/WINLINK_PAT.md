# Winlink via PAT

The UV-Pro's KISS TNC is only reachable over Bluetooth through BlueZ's
SerialPort profile, so the usual `kissattach /dev/rfcomm0` recipe doesn't work
with this radio. UVProTermBT bridges it: it exposes the radio's KISS stream as a
pty, runs `kissattach` on it to bring up the Linux **kernel AX.25** stack, and
then [PAT](https://getpat.io/) drives the radio over `ax25+linux`. PAT does all
the Winlink protocol (AX.25 + B2F); we just bridge the bytes.

## One-click setup (recommended)

1. Connect the radio in UVProTermBT (wait for **● BT** green).
2. Go to the **Winlink** tab and click **Start Winlink Bridge**.
   - The **first time**, it offers to install the Linux AX.25 tools
     (`ax25-tools`, ~1 MB) — click yes; you'll get a graphical password prompt.
   - It then creates a pty and runs `kissattach` for you (one more password
     prompt). When it's done, the tab shows the PAT settings to use.
3. In PAT, set the **AX.25 engine** to `linux` and the **axport** to `wl2k`
   (that's the port the app configured). Then connect to your RMS:
   ```
   pat connect "ax25+linux://wl2k/YOUR-RMS-CALL"
   ```
   or start the PAT web UI (`pat http`) and connect there.
4. Click **Stop Winlink Bridge** when done — it brings the AX.25 port down and
   hands the radio back to Chat/APRS/BBS.

While the bridge is up, **PAT drives the radio** and UVProTermBT's own transmit
is paused (one radio, one master).

### Winlink callsign

Winlink is commonly used on a distinct SSID (e.g. `-10`) from your APRS/BBS
call. Set **Winlink callsign** in File → Settings; leave it blank to use your
main callsign. That callsign is what the AX.25 port (and PAT) uses.

## What the app does for you

The **Start Winlink Bridge** button runs a small privileged helper
(`scripts/winlink-helper.sh`) via `pkexec` (PolicyKit — the graphical password
prompt). It: installs `ax25-tools`/`libax25` if missing, `modprobe ax25`, adds a
one-line `/etc/ax25/axports` entry for port `wl2k` with your Winlink callsign,
and runs `kissattach` on the app's pty. Stop runs the helper's `detach` to bring
the port down.

## Advanced / other clients

The app also has a raw **KISS-over-TCP** bridge (`uvprotermbt/kiss_tcp.py`,
`127.0.0.1:8001`) for KISS-TCP-capable clients:

- **pat-gensio** (PAT with built-in AX.25): connect gensio `kiss,tcp,localhost,8001`
  — no `kissattach`, no sudo.
- **tncattach** (`-T --kisstcp -H localhost -P 8001`): IP/Reticulum over the
  UV-Pro (not Winlink — tncattach makes an IP interface, not AX.25).
- Manual kernel AX.25: `socat pty,link=$HOME/uvpro-kiss,raw,echo=0
  tcp:127.0.0.1:8001` then `sudo kissattach $HOME/uvpro-kiss wl2k`.
  (Note: don't put the socat symlink in `/tmp` — `fs.protected_symlinks`
  blocks root from following it there.)

## Troubleshooting

- **kissattach failed:** make sure **● BT** is green (the radio's KISS TNC must
  be up) before starting the bridge.
- **No password prompt:** you need a PolicyKit agent (KDE/GNOME provide one).
- **Slow / retries:** 1200-baud packet is slow, and channel contention (beacons)
  hurts. PAT's AX.25 handles retries; use a quiet frequency.

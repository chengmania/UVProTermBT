# Talking KISS to a BTech UV‑Pro / VGC VR‑N76 over Bluetooth on Linux

*A practical, tested guide to sending and receiving AX.25/APRS packets
through the radio's built‑in KISS TNC over classic Bluetooth on Linux —
without direwolf's TX limitation, without a USB cable, and without the
raw‑socket dead ends most first attempts hit.*

The **BTech UV‑Pro** and **VGC VR‑N76** are the same radio with the same
firmware (the "Benshi" family, which also includes the RadioOddity GA‑5WB
and BTech GMRS‑Pro). Everything here applies to all of them.

> **Status:** RX and TX both verified end‑to‑end on Linux (Kubuntu, BlueZ
> 5.72) on 2026‑07‑14 — live Mic‑E beacons decoded, and an APRS message
> transmitted through the radio and decoded off‑air by direwolf. The
> reference script below is the exact code that was tested.

---

## TL;DR

1. On the radio: **Settings → General Settings → KISS TNC → Enable KISS TNC**.
2. `sudo apt install python3-dbus python3-gi`
3. Pair the radio to your computer (it shows up as a headset — that's fine).
4. Connect using BlueZ's **SerialPort profile**, *not* a raw RFCOMM socket
   to a guessed channel. Register an `org.bluez.Profile1` and let BlueZ hand
   you the RFCOMM file descriptor. Run [`uvpro_kiss_demo.py`](#the-script).

The one thing that trips everyone up: **a raw socket to "RFCOMM channel 1"
does not work.** Keep reading for why.

---

## Why the obvious approach fails

Almost every first attempt (mine included) tries one of these, and they all
fail unpredictably:

- `socket(AF_BLUETOOTH, …, BTPROTO_RFCOMM).connect((mac, 1))`
- `sudo rfcomm bind /dev/rfcomm0 <MAC> 1` then open the tty
- guessing the channel from an `sdptool` scan

Symptoms: the connect **times out**, or is **actively refused**, seemingly
at random. On a clean bond with KISS TNC enabled, I swept RFCOMM channels
1–15 and every one either refused (when the radio's Bluetooth was awake) or
timed out (it power‑saves its classic‑BT radio between connection attempts).

The reason: **the radio only serves its KISS session through its advertised
SerialPort profile (UUID `0x1101`), reached via an SDP‑negotiated profile
connection — not a bare channel connect.** This is exactly what the two
platforms where it "just works" already do:

- **macOS (YAAC):** you pair the radio and macOS auto‑mounts it as a serial
  device (`/dev/tty.VR-N76`). YAAC opens that. The OS did the SDP/profile
  negotiation for you.
- **Android (WoAD, the official app):** connects via the Serial Port
  Profile through Android's Bluetooth stack.

Any "RFCOMM channel N" you see in a packet capture is just the channel that
*that platform's profile negotiation* happened to land on — it is not a
fixed number you can hard‑code, and a raw socket can't reproduce it.

On Linux the equivalent of "let the OS mount the serial port" is to register
a BlueZ **`org.bluez.Profile1`** (client role, SerialPort UUID), ask BlueZ
to connect the device, and read the RFCOMM **file descriptor** BlueZ passes
to your profile's `NewConnection` callback. That's what the script does.
It's pure Python over D‑Bus — **no `sudo`, no `rfcomm` tool, no pyserial.**

---

## Step 1 — Enable KISS TNC on the radio

On the radio itself:

> **Settings** (green button) → **General Settings** → **KISS TNC** →
> **Enable KISS TNC**

This is required. Until it's on, the radio refuses the profile connection
and no channel carries KISS. (Note: this menu is a firmware feature that is
**not** in older printed manuals — make sure your firmware is current.)

For **transmit**, the radio also needs to be on the channel/VFO you want to
transmit on, and for APRS position beacons it needs a **GPS lock**. For
receive, just tune it to the frequency you want to monitor (e.g. 144.390
MHz in North America).

---

## Step 2 — Install the Bluetooth bindings

The script uses the system BlueZ D‑Bus bindings (they are *not* pip
packages):

```bash
sudo apt install python3-dbus python3-gi
```

(Equivalents on other distros: `python-dbus`/`python-gobject` on Arch,
`python3-dbus`/`python3-gobject` on Fedora.)

---

## Step 3 — Pair the radio

Put the radio into pairing mode (**Settings → Pairing**; the top LED
flashes red/green), then pair from your desktop's Bluetooth settings **or**
the command line:

```bash
bluetoothctl
# in the prompt:
scan on
# wait for your radio's MAC to appear, then:
pair  <MAC>
trust <MAC>
quit
```

The radio will show up as a **headset / audio device** — that is expected
and harmless. You do **not** need it connected for audio; the script makes
its own connection.

### If pairing or connecting misbehaves — the stale‑key gotcha

The single most confusing failure is a **stale link key**: if you (or the
radio) previously paired and then only *one* side forgot the bond, you get
`AuthenticationRejected` when pairing or `br-connection-refused` when
connecting. `bluetoothctl remove` silently does **nothing** if the device
isn't currently visible, so the old key lingers on disk.

Fix — wipe the bond on **both** sides and start fresh:

```bash
# On the radio: forget all Bluetooth connections, then re-enter Pairing mode.
# On the computer:
sudo rm -rf /var/lib/bluetooth/<ADAPTER-MAC>/<RADIO-MAC>
sudo systemctl restart bluetooth
bluetoothctl        # then: scan on / pair <MAC> / trust <MAC> / quit
```

(Find your adapter MAC with `bluetoothctl show`.) Let your desktop's own
Bluetooth agent handle the pairing prompt — don't try to register your own
agent in `bluetoothctl`, it'll fail because one already exists.

---

## Step 4 — Run the script

```bash
# Receive: print decoded AX.25 headers of everything the radio hears
python3 uvpro_kiss_demo.py <MAC> rx

# Transmit an APRS status report through the radio
python3 uvpro_kiss_demo.py <MAC> tx "YOURCALL-7" "hello from a UV-Pro"
```

Expected RX output (a real Mic‑E position beacon):

```
[connected] RFCOMM fd 9
[rx] KC3SMW-7 > TPQS3U-0 via WIDE1-1,WIDE2-1
     a8a0a2a666aa60968666a69aaeee...
```

For TX, monitor the frequency with another radio + direwolf (or any TNC)
and you'll see your frame decoded off‑air, e.g.:

```
KC3SMW-7>APZUVT:>hello from a UV-Pro
```

> **On direwolf and TX:** direwolf's *transmit* path is known not to work
> with this radio (it receives fine, but keying TX silently fails). That
> limitation does **not** apply here — the *radio* transmits; direwolf is
> only used to *receive* and confirm your packet. Sending KISS to the radio
> over Bluetooth keys it correctly.

---

## The script

`uvpro_kiss_demo.py` — self‑contained (only needs `python3-dbus` +
`python3-gi`). It shows the whole method: KISS framing, minimal AX.25 UI
encode/decode, and — the important part — the BlueZ SerialPort profile
connection.

The essential pieces:

```python
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"   # standard Serial Port

class Profile(dbus.service.Object):
    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}",
                         out_signature="")
    def NewConnection(self, path, fd, props):
        real_fd = fd.take()          # <-- BlueZ hands you the RFCOMM socket
        # read KISS frames from real_fd, or os.write() KISS frames to TX

# Register as a client SerialPort profile. AutoConnect=True is ESSENTIAL:
# it makes BlueZ attach *your* profile when the device connects. Without it,
# if the device is already connected (desktops often auto-connect it),
# NewConnection never fires.
mgr.RegisterProfile("/org/bluez/uvpro_demo", SPP_UUID,
    {"Role": "client", "Name": "uvpro-demo", "AutoConnect": dbus.Boolean(True)})

# Then Disconnect() (clears any stuck state) and Connect(); the NewConnection
# callback firing — not Connect()'s return value — is the real success signal.
```

See the full file in this repository:
[`scripts/uvpro_kiss_demo.py`](../scripts/uvpro_kiss_demo.py).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Connect **times out** on a raw socket to channel 1/4/etc. | You're using a raw socket. Use the SerialPort **profile** instead (this guide). |
| `NewConnection` never fires, but `Connect()` returns OK | The device is already connected and your profile isn't set to `AutoConnect: True`. Add it. |
| `org.bluez.Error.Failed: br-connection-busy` | Something else is mid‑connect (desktop auto‑connect, a stale `rfcomm bind`, a prior attempt). The script issues `Disconnect()` before `Connect()` to clear it; just let it retry. |
| `bluetoothctl connect` says `br-connection-refused` | Expected — it's trying the audio/headset profile, which the radio refuses. The SerialPort profile still works. |
| `AuthenticationRejected` when pairing | Stale link key. Wipe the bond on both sides (see Step 3) and re‑pair. |
| `sdptool browse`/`records` times out | Common with this radio; ignore it. BlueZ's own SDP during `Connect()` is what matters. |
| Connects, then immediately disconnects once, then works | Normal — the radio HUPs once right after the first connect. Just reconnect (the script does). |
| Nothing received even though connected | KISS TNC not enabled on the radio, or the radio isn't tuned to a channel with traffic. |
| Nothing transmitted | Radio not on a TX‑capable channel/VFO; for position beacons, no GPS lock. |

---

## How KISS/AX.25 looks on the wire (for the curious)

- **KISS**: each frame is `C0 00 <payload> C0`. `C0` (FEND) delimits frames,
  `00` is "data, port 0". Bytes `C0`/`DB` inside the payload are escaped as
  `DB DC` / `DB DD`. No CRC — the TNC adds/checks the AX.25 FCS itself.
- **AX.25 UI frame** (all APRS): destination address, source address, then
  optional digipeaters, then control `0x03` (UI), PID `0xF0` (no layer 3),
  then the info field. Each address is 6 callsign characters shifted left one
  bit, plus an SSID byte (SSID in bits 1–4, extension bit 0 marking the last
  address).
- **APRS position beacons from this radio are usually Mic‑E encoded**: the
  latitude is packed into the *destination* address (so it looks like
  gibberish, e.g. `TPQS3U`), and the info field starts with `` ` `` or `'`.
  Plain messages use the `:ADDRESSEE :text{id` format.

---

## Credits

This method stands on the shoulders of several people and projects:

- **Andrew (KA2DDO)**, author of YAAC, for the tip that the VR‑N76 mounts as
  a serial port in KISS mode — the insight that it's a *profile*, not a raw
  channel.
- **`khusmann/benlink`** — a Python library reverse‑engineering this radio
  family; invaluable for understanding the Bluetooth protocol.
- The **HamRadioTech** (DC6AP) writeup and **`islandmagic/kiss-tnc-test`**
  guide, which documented the classic‑BT/KISS path and the direwolf‑TX
  caveat.
- **direwolf**, for making off‑air verification easy.

---

*Written up by Greg (KC3SMW) as part of the
[UVProTermBT](https://github.com/chengmania/UVProTermBT) project — an AX.25
packet messenger + terminal for the UV‑Pro on Linux. Corrections and
improvements welcome.*

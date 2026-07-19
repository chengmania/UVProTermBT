# UV-Pro Audio Channel + GAIA — for SSTV (TX/RX images)

Reverse-engineered from `chengmania/HTCommander` (a Flutter port of Ylian
Saint-Hilaire's HTCommander) to add an **SSTV tab** to UVProTermBT. The radio
carries digital packet over KISS **and** an SBC audio stream over a *separate*
RFCOMM channel — the two coexist (confirmed on air: SSTV worked with KISS TNC
still enabled), so we can do audio (SSTV) without disturbing the packet link.

## Two RFCOMM channels (both classic BT, SDP-resolved by UUID)

| Channel | UUID | Carries |
|---|---|---|
| Control (SPP) | `00001101-0000-1000-8000-00805F9B34FB` | KISS (our use) **or** GAIA control frames |
| Audio ("BS AOC") | `39144315-32FA-40DB-85ED-FBFEBA2D86E6` | SBC audio, 0x7e-framed |

Both are mounted the same way we already mount KISS (BlueZ SerialPort profile,
`ConnectProfile(uuid)` → `NewConnection` fd). The audio UUID is already visible in
the radio's `bluetoothctl info` (listed "Vendor specific"). See
`uvprotermbt/audio_link.py` (audio channel) and `uvprotermbt/link.py` (generic
transport, parameterized by UUID).

## Audio channel framing (HDLC-style — `uvprotermbt/audio_frame.py`)

```
0x7e  <command>  <payload…>  0x7e
```
- **`0x7e`** = frame flag (start and end; one `0x7e` closes a frame and opens the
  next). Runs of `0x7e` are empty frames → skipped.
- **`0x7d`** = escape: a literal `0x7d`/`0x7e` in the payload is sent as `0x7d`
  then `byte XOR 0x20`. Un-escape reverses it.
- First un-escaped byte = **command**; the rest = payload.

| Command | Meaning |
|---|---|
| `0x00` / `0x03` | received audio — payload = concatenated SBC frames |
| `0x01` | audio end (RX stream finished) |
| `0x02` | audio ACK |
| `0x09` | transmit-audio echo (our own TX looped back; decode for VU, don't play) |

**Stop-transmit frame** (send verbatim to end a TX):
`7e 01 00 01 00 00 00 00 00 00 7e` (`audio_frame.END_AUDIO_FRAME`).

## SBC format (the radio's exact params — `uvprotermbt/sbc.py`)

32 kHz · 16-bit · **mono** · **blocks 16** · **subbands 8** · allocation
**loudness** · **bitpool 40** · sync byte **`0x9C`**. PCM per SBC frame =
16·8·2 = **256 bytes** (128 samples ≈ 4 ms). Codec = **libsbc** (`libsbc.so.1`);
decode reads params from each frame header, so RX needs no config. Verified:
encode→decode round-trips at these params (`tests/test_sbc.py`).

## GAIA control frames (SPP channel) — PTT / radio control

```
FF 01 <flags> <dataLen> <grp_hi grp_lo> <cmd_hi cmd_lo> <data…>
```
Big-endian; `flags=0x00` = no checksum; `dataLen` = length of `data` only.
- **Groups:** `basic = 2`, `extended = 10`.
- **PTT** = `doProgFunc` (basic cmd **66 / 0x42**) with a `PFActionType` and a
  `PFEffectType`. Effects: `mainPtt = 13`, `toggleRadioTx = 4`. Actions:
  `short=1`, `lowToHigh=6` (press), `highToLow=7` (release). Example key frame:
  `FF 01 00 02  00 02  00 42  <action> <effect>` (exact combo TBD on air).

> Note: audio TX may be **implicit** — HTCommander's engine just streams audio
> frames and sends `END_AUDIO_FRAME` to stop, without an explicit PTT command.
> M2/M3 on-air testing decides whether a GAIA PTT is needed at all.

## Confirmed vs. needs on-air

- **Confirmed from code / bench:** both UUIDs; the 0x7e/0x7d framing + command
  bytes; `END_AUDIO_FRAME`; SBC params (round-trip verified with libsbc); GAIA
  frame layout and group/command/effect values.
- **Needs the radio to confirm:** (1) does opening the audio channel stream RX
  immediately or need an enable command; (2) implicit vs explicit PTT for TX;
  (3) the outgoing TX command byte; (4) KISS + GAIA coexistence on the SPP channel.

## Build status (branch `sstv-audio`)

- **M0 done:** this doc; `audio_frame.py` (+ tests), `sbc.py` (+ tests),
  `audio_link.py`; `link.py` generalized to any RFCOMM UUID.
- **M1 done + PROVEN ON AIR (2026-07-18):** `python -m uvprotermbt.audio_capture
  --seconds 30` opens the audio channel and decodes it to `uvpro-audio.wav`
  (+ `.sbc`). Greg captured his own voice off the radio, cleanly (282/282 SBC
  frames). The audio path is real on Linux. ✅
- **M2 done + PROVEN ON AIR (2026-07-18):** decoded a real received SSTV image
  off the air (`audio_capture --sstv`). `uvprotermbt/sstv.py` wraps encode
  (`pysstv`) + decode (colaclanth `sstv`). ✅
- **M3+M4 done + PROVEN ON AIR (2026-07-19):** `uvprotermbt/audio_tx.py` transmits
  audio/SSTV. Radio keys and transmits; a Robot36 image sent from the app was
  decoded on another device. Two byte-level bugs were blocking TX (found by
  diffing an HCI capture of HTCommander's Linux build against ours):
  1. **SBC allocation must be Loudness (0x00), not SNR** — sbc.py had the libsbc
     constant backwards, so we sent `9c 73…` frames the radio rejected (it wants
     `9c 71…`).
  2. **Audio frames must be small (~4 SBC frames / ~352 B)**, like HTCommander —
     we were sending 50-SBC-frame (~4400 B) frames the radio ignored.
  Keying is **implicit**: with the GAIA control channel open (the `getDevInfo` +
  `registerNotification` handshake — `audio_tx --control`), streaming correctly
  formatted audio keys the radio; no PTT command. The radio's htStatus
  notification flips its TX bit to `c0` while transmitting.
  Tools: `audio_tx --tone 1000 --seconds 5 --control`,
  `audio_tx --image pic.png --mode Robot36 --control`. ⚠ real RF.
- **Next:** M5 — the SSTV tab (RX images live + TX pick-image/mode/send), wiring
  the audio link + GAIA control alongside KISS. Master stays on v0.9.0 until M5.

### Prototype dependencies (not yet in requirements.txt)
- `pysstv` (PyPI) — SSTV **encode** (TX).
- colaclanth `sstv` — SSTV **decode**: `pip install
  git+https://github.com/colaclanth/sstv.git` (pulls numpy/scipy/soundfile;
  needs `libsndfile`). CLI-oriented; we wrap it and silence its TTY logging.
- `libsbc1` (system) — SBC codec, via ctypes in `sbc.py`.

For the shipped SSTV tab (M5) we may port HTCommander's numpy-only real-time
decoder to drop the git + scipy dependency.

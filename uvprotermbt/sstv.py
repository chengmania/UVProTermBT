"""SSTV encode/decode glue for UVProTermBT.

- Decode: wraps colaclanth's `sstv` decoder (numpy/scipy/soundfile) — turns a
  captured WAV (e.g. from audio_capture) into a PIL image.
- Encode: wraps `pysstv` — turns an image into SSTV audio (for TX, M4).

Both third-party deps are optional and guarded (like the WebEngine guard), so the
package imports without them; callers check ENCODE_AVAILABLE / DECODE_AVAILABLE.

Prototype note: the decoder is colaclanth/sstv (install:
`pip install git+https://github.com/colaclanth/sstv.git`), which is a file-based,
CLI-oriented decoder. For the shipped SSTV tab (M5) we may port HTCommander's
real-time decoder to drop the git/scipy dependency. See docs/GAIA_AUDIO_SSTV.md.
"""

from __future__ import annotations

from typing import Optional

# ---- decode (colaclanth sstv) --------------------------------------------
try:
    # colaclanth's sstv logs progress via os.get_terminal_size(), which raises
    # OSError with no TTY (GUI / pipe). common.py captures it with
    # `from os import get_terminal_size` at import, so patch it *before* importing
    # sstv, falling back to a fixed size instead of raising.
    import os as _os

    _orig_gts = _os.get_terminal_size

    def _safe_gts(*a):  # noqa: ANN
        try:
            return _orig_gts(*a)
        except OSError:
            return _os.terminal_size((80, 24))

    _os.get_terminal_size = _safe_gts

    import sstv.common as _sc  # noqa: E402
    import sstv.decode as _sd  # noqa: E402
    from sstv.decode import SSTVDecoder as _SSTVDecoder  # noqa: E402

    _noop = lambda *a, **k: None  # noqa: E731 - also silence its progress logger
    _sd.log_message = _noop
    _sc.log_message = _noop
    DECODE_AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional deps
    _SSTVDecoder = None  # type: ignore[assignment,misc]
    DECODE_AVAILABLE = False

# ---- encode (pysstv) ------------------------------------------------------
try:
    from pysstv import color as _pysstv_color

    # Build the mode map from pysstv's own MODES list so we only expose classes
    # it actually provides (keyed by class name, e.g. "Robot36", "MartinM1"…).
    ENCODE_MODES = {cls.__name__: cls for cls in _pysstv_color.MODES}
    ENCODE_AVAILABLE = bool(ENCODE_MODES)
except Exception:  # pragma: no cover
    ENCODE_MODES = {}
    ENCODE_AVAILABLE = False


def decode_wav(path: str):
    """Decode an SSTV WAV to a PIL.Image, or None if no image was found."""
    if not DECODE_AVAILABLE:
        raise RuntimeError("SSTV decoder not installed "
                           "(pip install git+https://github.com/colaclanth/sstv.git)")
    with open(path, "rb") as f:
        dec = _SSTVDecoder(f)
        try:
            return dec.decode()
        finally:
            dec.close()


def encode_wav(image, path: str, mode: str = "Robot36",
               sample_rate: int = 44100, bits: int = 16) -> None:
    """Encode a PIL.Image to an SSTV WAV using the named mode."""
    if not ENCODE_AVAILABLE:
        raise RuntimeError("pysstv not installed (pip install pysstv)")
    cls = ENCODE_MODES.get(mode)
    if cls is None:
        raise ValueError(f"unknown SSTV mode {mode!r}; have {sorted(ENCODE_MODES)}")
    cls(image.convert("RGB"), sample_rate, bits).write_wav(path)


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Decode an SSTV WAV to a PNG")
    ap.add_argument("wav", help="input WAV (e.g. uvpro-audio.wav)")
    ap.add_argument("-o", "--out", default=None, help="output PNG (default: <wav>.png)")
    args = ap.parse_args()
    out = args.out or (args.wav.rsplit(".", 1)[0] + ".png")
    img = decode_wav(args.wav)
    if img is None:
        raise SystemExit("no SSTV image found in the audio (no VIS header detected)")
    img.save(out)
    print(f"decoded {img.size[0]}x{img.size[1]} -> {out}")


if __name__ == "__main__":
    _main()

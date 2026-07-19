"""Round-trip test for the SSTV encode/decode glue (uvprotermbt/sstv.py).

Needs the optional deps (pysstv + colaclanth sstv); skipped otherwise, so CI
without them stays green. Proves the DSP works end-to-end without a radio:
image -> SSTV audio -> decoded image, matching within analog tolerance.
"""

from __future__ import annotations

import pytest

sstv = pytest.importorskip("uvprotermbt.sstv")

pytestmark = pytest.mark.skipif(
    not (sstv.ENCODE_AVAILABLE and sstv.DECODE_AVAILABLE),
    reason="pysstv and/or colaclanth sstv not installed")


def _mean_diff(a, b) -> float:
    a = a.convert("RGB").resize((160, 120))
    b = b.convert("RGB").resize((160, 120))
    pa, pb = list(a.getdata()), list(b.getdata())
    return sum(abs(x - y) for ta, tb in zip(pa, pb) for x, y in zip(ta, tb)) / (len(pa) * 3)


def test_encode_modes_present():
    assert "Robot36" in sstv.ENCODE_MODES


def test_roundtrip_robot36(tmp_path):
    from PIL import Image, ImageDraw
    src = Image.new("RGB", (320, 240), "darkblue")
    d = ImageDraw.Draw(src)
    d.rectangle([10, 10, 310, 70], fill="orange")
    d.ellipse([110, 110, 210, 210], fill="lime")

    wav = str(tmp_path / "rt.wav")
    sstv.encode_wav(src, wav, mode="Robot36")
    out = sstv.decode_wav(wav)

    assert out is not None
    assert out.size == (320, 240)
    # Analog SSTV: expect a close-but-not-exact match.
    assert _mean_diff(src, out) < 60.0

"""Light integration test for the macos_ocr wrapper.

These tests run on macOS only and exercise Apple's `VNRecognizeTextRequest`
end-to-end. They render a small known PNG with PIL, call recognize_text, and
assert the OCR returns the expected text + plausible bbox. Skipped on
non-macOS and when the hand-rendered image isn't recognizable (e.g. CI
runners without GPU support).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS-only test (Vision.framework)", allow_module_level=True)

PIL = pytest.importorskip("PIL.Image")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

try:
    from omniclaw.tools import macos_ocr
except ImportError as exc:
    pytest.skip(f"macos_ocr unavailable: {exc}", allow_module_level=True)


def _render_text_png(path: Path, text: str, *, width: int = 800, height: int = 200) -> Path:
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Try several common Mac system fonts; fall back to default if none are
    # found (default is too tiny for Vision to read reliably, hence the skip).
    for candidate in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]:
        try:
            font = ImageFont.truetype(candidate, 64)
            break
        except OSError:
            font = None
    if font is None:
        font = ImageFont.load_default()
    draw.text((40, 60), text, fill=(0, 0, 0), font=font)
    img.save(path, format="PNG")
    return path


def test_recognize_text_finds_simple_string(tmp_path):
    image = _render_text_png(tmp_path / "hello.png", "BLR - Team")
    out = macos_ocr.recognize_text(image)
    assert out["ok"] is True, out
    assert out["image_width"] == 800
    assert out["image_height"] == 200
    items = out["items"]
    if not items:
        pytest.skip("Vision did not produce results on this runner (likely default font)")
    # Find any item whose text contains 'BLR'
    matches = [it for it in items if "BLR" in it["text"]]
    assert matches, f"expected 'BLR' in OCR output, got {[it['text'] for it in items]}"
    bx, by, bw, bh = matches[0]["bbox"]
    # Bbox is in image-pixel TOP-LEFT coords; should be inside the image.
    assert 0 <= bx < 800
    assert 0 <= by < 200
    assert bw > 0
    assert bh > 0
    assert matches[0]["confidence"] > 0.5


def test_recognize_text_image_missing(tmp_path):
    out = macos_ocr.recognize_text(tmp_path / "nope.png")
    assert out["ok"] is False
    assert "image not found" in out["error"]


def test_recognize_text_corrupt_image(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    out = macos_ocr.recognize_text(bad)
    assert out["ok"] is False
    assert "could not" in out["error"].lower() or "decode" in out["error"].lower()


def test_recognize_text_returns_top_left_bbox(tmp_path):
    """Vision natively uses normalized bottom-left bboxes; we flip to top-left
    pixel coords. Regression: ensure 'BLR - Team' rendered ~y=60 lands near the
    top of the image, not near the bottom."""
    image = _render_text_png(tmp_path / "y.png", "BLR - Team")
    out = macos_ocr.recognize_text(image)
    if not out["ok"] or not out["items"]:
        pytest.skip("Vision did not produce results on this runner")
    matches = [it for it in out["items"] if "BLR" in it["text"]]
    if not matches:
        pytest.skip("BLR not detected on this runner")
    _, by, _, bh = matches[0]["bbox"]
    # The text was drawn at y=60 with 64-pt font, so bbox top should be in the
    # upper half of a 200-pixel image.
    assert by < 150, f"bbox y={by} suggests bottom-left coords leaked through"

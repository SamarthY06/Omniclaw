"""Unit tests for `text_locate` (macos_vision.call_text_locate).

`call_text_locate` is the on-device OCR-driven locator. It calls
`omniclaw.tools.macos_ocr.recognize_text` to get text + bboxes, then runs
fuzzy matching to pick the best click point. We mock `recognize_text` so the
tests are platform-agnostic (no pyobjc, no Vision.framework needed).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from omniclaw.tools import macos_vision


# ----- helpers --------------------------------------------------------------


_PNG_4x2 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000040000000208060000007f15c4"
    "f60000001049444154789c63fcffff3f0306060300000d3f014ee2dd000d0000"
    "000049454e44ae426082"
)


def _make_png(path: Path) -> Path:
    path.write_bytes(_PNG_4x2)
    return path


def _ocr_result(items, *, image_width: int = 3418, image_height: int = 2008) -> dict:
    return {
        "ok": True,
        "image_width": image_width,
        "image_height": image_height,
        "items": items,
    }


@pytest.fixture
def mock_ocr(monkeypatch):
    """Patch `_import_macos_ocr` to return a fake module."""
    state: dict = {"return_value": _ocr_result([])}

    class _FakeMod:
        @staticmethod
        def recognize_text(image_path):
            state["called_with"] = image_path
            return state["return_value"]

    monkeypatch.setattr(macos_vision, "_import_macos_ocr", lambda: _FakeMod)
    return state


# ----- _score: scoring sanity -----------------------------------------------


def test_score_exact_match():
    assert macos_vision._score("BLR - Team", "BLR - Team") == 1.0
    assert macos_vision._score("BLR - Team", "blr - team") == 1.0


def test_score_substring_target_in_text():
    s = macos_vision._score("BLR", "BLR - Team chat")
    assert 0.7 <= s <= 1.0


def test_score_reverse_substring():
    s = macos_vision._score("BLR - Team chat row", "BLR - Team")
    assert 0.5 <= s < 1.0


def test_score_unrelated_strings_low():
    s = macos_vision._score("BLR - Team", "Spotify")
    assert s < 0.3


def test_score_empty_inputs_zero():
    assert macos_vision._score("", "x") == 0.0
    assert macos_vision._score("x", "") == 0.0


# ----- happy paths ----------------------------------------------------------


def test_text_locate_finds_exact_match(tmp_path, mock_ocr):
    img = _make_png(tmp_path / "shot.png")
    mock_ocr["return_value"] = _ocr_result([
        {"text": "Activity", "confidence": 1.0, "bbox": (25, 239, 65, 15)},
        {"text": "BLR - Team", "confidence": 1.0, "bbox": (243, 815, 139, 25)},
        {"text": "Copilot", "confidence": 1.0, "bbox": (25, 706, 60, 10)},
    ])

    out = macos_vision.call_text_locate(
        image_path=img,
        target="BLR - Team",
        screen_width=1728,
        screen_height=1117,
    )

    assert out["ok"] is True
    assert out["found"] is True
    assert out["matched_text"] == "BLR - Team"
    assert out["match_score"] == 1.0
    assert out["ocr_confidence"] == 1.0
    # Image-space center of (243, 815, 139, 25) = (243+69=312, 815+12=827 or 828)
    assert 311 <= out["image_x"] <= 313
    assert 826 <= out["image_y"] <= 829
    # Screen-space rescale: 312 * 1728 / 3418 ~= 158, 828 * 1117 / 2008 ~= 461
    assert 156 <= out["click_x"] <= 160
    assert 459 <= out["click_y"] <= 462
    # Bounding box is preserved
    assert out["bbox"] == [243, 815, 139, 25]
    # Top candidate is BLR - Team, others appear too
    assert out["candidates"][0]["text"] == "BLR - Team"
    assert any(c["text"] == "Activity" for c in out["candidates"]) or len(out["candidates"]) <= 2


def test_text_locate_substring_target(tmp_path, mock_ocr):
    img = _make_png(tmp_path / "shot.png")
    mock_ocr["return_value"] = _ocr_result([
        {"text": "BLR - Team", "confidence": 1.0, "bbox": (243, 815, 139, 25)},
    ])
    out = macos_vision.call_text_locate(image_path=img, target="BLR")
    assert out["ok"] is True
    assert out["found"] is True
    assert out["matched_text"] == "BLR - Team"
    # Substring match: target "BLR" is 3/10 of "BLR - Team", clamped to 0.7
    assert out["match_score"] == pytest.approx(0.7, abs=1e-6)


def test_text_locate_no_screen_dims_returns_image_coords_only(tmp_path, mock_ocr):
    img = _make_png(tmp_path / "shot.png")
    mock_ocr["return_value"] = _ocr_result([
        {"text": "BLR - Team", "confidence": 1.0, "bbox": (243, 815, 139, 25)},
    ])
    out = macos_vision.call_text_locate(image_path=img, target="BLR - Team")
    assert "click_x" not in out
    assert "click_y" not in out
    assert out["image_x"] == 243 + 69       # 312 = floor((243 + 139/2))
    assert out["image_y"] == round(815 + 25 / 2)  # 828 (banker's rounding of 827.5)


def test_text_locate_score_penalized_by_low_ocr_confidence(tmp_path, mock_ocr):
    img = _make_png(tmp_path / "shot.png")
    mock_ocr["return_value"] = _ocr_result([
        # Higher OCR confidence on the right answer, even if similarity is the same
        {"text": "BLR - Team", "confidence": 1.0, "bbox": (243, 815, 139, 25)},
        # Decoy with same text but very low OCR confidence
        {"text": "BLR - Team", "confidence": 0.1, "bbox": (10, 10, 20, 20)},
    ])
    out = macos_vision.call_text_locate(image_path=img, target="BLR - Team")
    assert out["found"] is True
    # The high-confidence one should win
    assert out["bbox"] == [243, 815, 139, 25]


def test_text_locate_threshold_no_match(tmp_path, mock_ocr):
    img = _make_png(tmp_path / "shot.png")
    mock_ocr["return_value"] = _ocr_result([
        {"text": "Activity", "confidence": 1.0, "bbox": (25, 239, 65, 15)},
        {"text": "Calls", "confidence": 1.0, "bbox": (35, 328, 45, 20)},
    ])
    out = macos_vision.call_text_locate(
        image_path=img,
        target="BLR - Team",
        min_score=0.7,
    )
    assert out["ok"] is True
    assert out["found"] is False
    assert "click_x" not in out
    assert "matched_text" not in out
    assert out["candidates"]  # candidates still listed for debugging
    assert out["min_score"] == 0.7


def test_text_locate_max_candidates_caps_list(tmp_path, mock_ocr):
    img = _make_png(tmp_path / "shot.png")
    mock_ocr["return_value"] = _ocr_result([
        {"text": f"item-{i}", "confidence": 0.9, "bbox": (i, i, 10, 10)}
        for i in range(20)
    ])
    out = macos_vision.call_text_locate(
        image_path=img,
        target="item-5",
        max_candidates=3,
    )
    assert len(out["candidates"]) == 3


# ----- error paths ----------------------------------------------------------


def test_text_locate_image_missing(tmp_path):
    out = macos_vision.call_text_locate(
        image_path=tmp_path / "nope.png",
        target="x",
    )
    assert out["ok"] is False
    assert "image not found" in out["error"]


def test_text_locate_ocr_failure_propagates(tmp_path, mock_ocr):
    img = _make_png(tmp_path / "shot.png")
    mock_ocr["return_value"] = {"ok": False, "error": "Vision request failed: foo"}
    out = macos_vision.call_text_locate(image_path=img, target="x")
    assert out["ok"] is False
    assert "Vision request failed" in out["error"]


def test_text_locate_handles_missing_macos_ocr(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")

    def _raise():
        raise ImportError("pyobjc-framework-Vision not installed")

    monkeypatch.setattr(macos_vision, "_import_macos_ocr", _raise)
    out = macos_vision.call_text_locate(image_path=img, target="x")
    assert out["ok"] is False
    assert "macos_ocr unavailable" in out["error"]


# ----- --json-tools surfaces text_locate -----------------------------------


def test_json_tools_includes_text_locate():
    schemas = macos_vision._TOOL_SCHEMAS
    by_name = {s["name"]: s for s in schemas}
    assert "text_locate" in by_name
    tl = by_name["text_locate"]
    assert tl["sensitivity"] == "S0"
    assert tl["parameters"]["required"] == ["image", "target"]
    props = tl["parameters"]["properties"]
    assert "screen_width" in props
    assert "screen_height" in props
    assert "min_score" in props

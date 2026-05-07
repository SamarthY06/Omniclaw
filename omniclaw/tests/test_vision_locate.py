"""Unit tests for the new `vision_locate` (OpenAI GA `computer` tool, gpt-5.5).

The previous implementation asked gpt-4o for normalized fractions, which was
fundamentally unreliable. The new implementation runs OpenAI's purpose-trained
GA `computer` tool via the /v1/responses endpoint and parses the returned
`computer_call.actions[0]` (a `click` with image-pixel x,y).

Network calls are mocked at urllib.request.urlopen so the tests are hermetic.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from omniclaw.tools import macos_vision


# ----- helpers --------------------------------------------------------------


@contextmanager
def _mock_urlopen(monkeypatch, *, responses: list[dict] | None = None):
    """Replace urlopen with a fake that returns successive responses per call."""
    captured: dict = {"calls": []}
    iterator = iter(responses or [])

    class _FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        captured["calls"].append({
            "url": req.full_url,
            "body": body,
            "timeout": timeout,
        })
        try:
            payload = next(iterator)
        except StopIteration as exc:
            raise AssertionError(
                "urlopen called more times than _mock_urlopen has responses"
            ) from exc
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(macos_vision.urllib.request, "urlopen", _fake_urlopen)
    yield captured


# Real, valid 4x2 PNG (so _read_png_size returns 4, 2).
_PNG_4x2 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000040000000208060000007f15c4"
    "f60000001049444154789c63fcffff3f0306060300000d3f014ee2dd000d0000"
    "000049454e44ae426082"
)


def _make_png(path: Path) -> Path:
    path.write_bytes(_PNG_4x2)
    return path


def _screenshot_request_response() -> dict:
    return {
        "id": "resp_turn1",
        "model": "gpt-5.5-test",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "computer_call",
                "id": "cu_1",
                "status": "completed",
                "call_id": "call_screenshot",
                "actions": [{"type": "screenshot"}],
            },
        ],
    }


def _click_response(*, x: int = 1, y: int = 1, button: str = "left") -> dict:
    return {
        "id": "resp_turn2",
        "model": "gpt-5.5-test",
        "output": [
            {"type": "reasoning", "id": "rs_2", "summary": []},
            {
                "type": "computer_call",
                "id": "cu_2",
                "status": "completed",
                "call_id": "call_click",
                "actions": [{
                    "type": "click",
                    "button": button,
                    "keys": None,
                    "x": x,
                    "y": y,
                }],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-locate")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)


# ----- _read_png_size (kept; still used) -----------------------------------


def test_read_png_size_returns_dims(tmp_path):
    path = _make_png(tmp_path / "x.png")
    assert macos_vision._read_png_size(path) == (4, 2)


def test_read_png_size_rejects_non_png(tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"this is not a png")
    with pytest.raises(ValueError, match="not a PNG"):
        macos_vision._read_png_size(path)


# ----- happy path: 2-turn dance returns click coords ------------------------


def test_locate_two_turn_dance_returns_image_coords(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")
    with _mock_urlopen(
        monkeypatch,
        responses=[_screenshot_request_response(), _click_response(x=2, y=1)],
    ) as cap:
        out = macos_vision.call_vision_locate(image_path=img, target="X")

    assert out["ok"] is True
    assert out["found"] is True
    assert out["image_x"] == 2
    assert out["image_y"] == 1
    assert out["image_width"] == 4
    assert out["image_height"] == 2
    assert "click_x" not in out  # no screen dims given
    assert "click_y" not in out
    assert out["raw_action"]["type"] == "click"
    assert out["raw_action"]["x"] == 2

    # Two POSTs: turn 1 (no screenshot) then turn 2 (with screenshot)
    assert len(cap["calls"]) == 2
    assert cap["calls"][0]["url"] == macos_vision.OPENAI_RESPONSES_URL
    assert cap["calls"][0]["body"]["tools"] == [{"type": "computer"}]
    assert cap["calls"][0]["body"]["model"] == macos_vision.DEFAULT_LOCATE_MODEL
    # Turn 2 feeds the screenshot back as computer_call_output
    turn2_input = cap["calls"][1]["body"]["input"]
    assert turn2_input[0]["type"] == "computer_call_output"
    assert turn2_input[0]["call_id"] == "call_screenshot"
    assert turn2_input[0]["output"]["type"] == "computer_screenshot"
    assert turn2_input[0]["output"]["image_url"].startswith("data:image/png;base64,")
    assert cap["calls"][1]["body"]["previous_response_id"] == "resp_turn1"


def test_locate_with_screen_dims_scales_to_screen_points(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")  # 4x2 image
    with _mock_urlopen(
        monkeypatch,
        responses=[_screenshot_request_response(), _click_response(x=2, y=1)],
    ):
        out = macos_vision.call_vision_locate(
            image_path=img,
            target="X",
            screen_width=1728,
            screen_height=1117,
        )
    assert out["screen_width"] == 1728
    assert out["screen_height"] == 1117
    # 2 / 4 * 1728 = 864; 1 / 2 * 1117 = 558.5 -> 558 (banker's rounding)
    assert out["click_x"] == 864
    assert out["click_y"] == round(1 * 1117 / 2)  # 559 with normal rounding


def test_locate_short_circuits_when_first_turn_already_clicks(tmp_path, monkeypatch):
    """If the model emits a click on turn 1 (rare), we don't need turn 2."""
    img = _make_png(tmp_path / "shot.png")
    only_click = {
        "id": "resp_only",
        "model": "gpt-5.5-test",
        "output": [
            {
                "type": "computer_call",
                "id": "cu_x",
                "status": "completed",
                "call_id": "call_x",
                "actions": [{"type": "click", "button": "left", "x": 3, "y": 1}],
            }
        ],
    }
    with _mock_urlopen(monkeypatch, responses=[only_click]) as cap:
        out = macos_vision.call_vision_locate(image_path=img, target="X")
    assert out["ok"] is True
    assert out["image_x"] == 3
    assert len(cap["calls"]) == 1


# ----- error paths ----------------------------------------------------------


def test_locate_image_missing(tmp_path):
    out = macos_vision.call_vision_locate(
        image_path=tmp_path / "nonexistent.png",
        target="x",
    )
    assert out["ok"] is False
    assert "image not found" in out["error"]


def test_locate_no_api_key(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(macos_vision, "_resolve_api_key", lambda: None)
    out = macos_vision.call_vision_locate(image_path=img, target="x")
    assert out["ok"] is False
    assert "OPENAI_API_KEY" in out["error"]


def test_locate_no_computer_call_in_response(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")
    bad = {
        "id": "resp_bad",
        "model": "gpt-5.5-test",
        "output": [{"type": "message", "content": "I refuse to click."}],
    }
    with _mock_urlopen(monkeypatch, responses=[bad]):
        out = macos_vision.call_vision_locate(image_path=img, target="x")
    assert out["ok"] is False
    assert "no computer_call" in out["error"]


def test_locate_no_click_action_after_screenshot(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")
    weird_turn2 = {
        "id": "resp_weird",
        "model": "gpt-5.5-test",
        "output": [
            {
                "type": "computer_call",
                "id": "cu_w",
                "status": "completed",
                "call_id": "call_w",
                "actions": [{"type": "scroll", "direction": "down"}],
            }
        ],
    }
    with _mock_urlopen(
        monkeypatch,
        responses=[_screenshot_request_response(), weird_turn2],
    ):
        out = macos_vision.call_vision_locate(image_path=img, target="x")
    assert out["ok"] is False
    assert "no click action" in out["error"]


# ----- request shape sanity --------------------------------------------------


def test_locate_first_turn_payload_uses_computer_tool(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")
    with _mock_urlopen(
        monkeypatch,
        responses=[_screenshot_request_response(), _click_response()],
    ) as cap:
        macos_vision.call_vision_locate(
            image_path=img,
            target="the BLR - Team chat row in the left chat list",
        )
    body1 = cap["calls"][0]["body"]
    assert body1["tools"] == [{"type": "computer"}]
    assert body1["model"] == macos_vision.DEFAULT_LOCATE_MODEL
    user_msg = body1["input"][0]
    assert user_msg["role"] == "user"
    text_part = next(p for p in user_msg["content"] if p["type"] == "input_text")
    assert "BLR - Team chat row" in text_part["text"]


def test_locate_authorization_header_present(tmp_path, monkeypatch):
    img = _make_png(tmp_path / "shot.png")
    captured: dict = {}

    class _FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload
        def read(self) -> bytes:
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResp(json.dumps(_screenshot_request_response()).encode())

    monkeypatch.setattr(macos_vision.urllib.request, "urlopen", _fake_urlopen)
    macos_vision.call_vision_locate(image_path=img, target="x")
    assert captured["headers"]["authorization"] == "Bearer sk-test-locate"


# ----- --json-tools surfaces vision_locate ---------------------------------


def test_json_tools_includes_vision_locate():
    schemas = macos_vision._TOOL_SCHEMAS
    names = {s["name"] for s in schemas}
    assert "vision_read" in names
    assert "text_locate" in names
    assert "vision_locate" in names
    locate = next(s for s in schemas if s["name"] == "vision_locate")
    assert locate["sensitivity"] == "S2"
    assert "image" in locate["parameters"]["required"]
    assert "target" in locate["parameters"]["required"]
    assert locate["parameters"]["properties"]["model"]["default"] == macos_vision.DEFAULT_LOCATE_MODEL

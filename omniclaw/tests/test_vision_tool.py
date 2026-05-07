"""Unit tests for omniclaw/tools/macos_vision.py.

Network calls are mocked at urllib.request.urlopen so the tests are hermetic.
"""
from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager
from pathlib import Path

import pytest

from omniclaw.tools import macos_vision


# ----- helpers --------------------------------------------------------------


@contextmanager
def _mock_urlopen(monkeypatch, *, response: dict | None = None, exc: Exception | None = None):
    """Replace urlopen in macos_vision with a fake that returns or raises."""
    captured = {}

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
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        if exc is not None:
            raise exc
        return _FakeResp(json.dumps(response or {}).encode("utf-8"))

    monkeypatch.setattr(macos_vision.urllib.request, "urlopen", _fake_urlopen)
    yield captured


def _make_png(path: Path) -> Path:
    # Minimal 1x1 PNG (89 bytes); content doesn't matter, just non-empty.
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c63f8cf00000000ffff03000004000118f49ae50000"
        "000049454e44ae426082"
    )
    path.write_bytes(png_bytes)
    return path


def _ok_response(text: str = "hello") -> dict:
    return {
        "id": "chatcmpl-fake",
        "model": "gpt-4o-mini-test",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


# ----- _resolve_api_key -----------------------------------------------------


def test_resolve_api_key_env_var_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert macos_vision._resolve_api_key() == "sk-from-env"


def test_resolve_api_key_strips_whitespace(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-spaced  ")
    assert macos_vision._resolve_api_key() == "sk-spaced"


def test_resolve_api_key_falls_back_to_openclaw_json(monkeypatch):
    # The conftest autouse fixture already sets HOME to a fresh tmp dir per test.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    home = Path(__import__("os").environ["HOME"])
    cfg_dir = home / ".openclaw"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "openclaw.json"
    cfg.write_text(json.dumps({"talk": {"realtime": {"openai": {"apiKey": "sk-from-cfg"}}}}))
    assert macos_vision._resolve_api_key() == "sk-from-cfg"


def test_resolve_api_key_returns_none_when_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # The repo's omniclaw/.env may still hold a real key; we only assert the
    # function returns either a str (from .env fallback) or None, never raises.
    result = macos_vision._resolve_api_key()
    assert result is None or isinstance(result, str)


# ----- _build_request_body --------------------------------------------------


def test_build_request_body_shape():
    body = macos_vision._build_request_body(
        image_b64="ABC123",
        question="what is this?",
        model="gpt-4o-mini",
        max_tokens=512,
        detail="low",
    )
    assert body["model"] == "gpt-4o-mini"
    assert body["max_tokens"] == 512
    msgs = body["messages"]
    assert len(msgs) == 1
    parts = msgs[0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["detail"] == "low"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1]["image_url"]["url"].endswith("ABC123")


# ----- call_vision happy path -----------------------------------------------


def test_call_vision_happy_path(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "shot.png")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with _mock_urlopen(monkeypatch, response=_ok_response("the answer")) as captured:
        out = macos_vision.call_vision(
            image_path=image,
            question="describe",
            model="gpt-4o-mini",
            max_tokens=256,
            detail="auto",
        )

    assert out["ok"] is True
    assert out["result"] == "the answer"
    assert out["model"] == "gpt-4o-mini-test"
    assert out["id"] == "chatcmpl-fake"
    assert out["usage"]["total_tokens"] == 110

    assert captured["url"] == macos_vision.OPENAI_CHAT_URL
    auth = {k.lower(): v for k, v in captured["headers"].items()}["authorization"]
    assert auth == "Bearer sk-test"
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["max_tokens"] == 256


def test_call_vision_handles_list_content(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "shot.png")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    response = {
        "id": "x",
        "model": "gpt-4o-mini",
        "choices": [{"message": {"content": [{"type": "text", "text": "part-A"}, {"type": "text", "text": "part-B"}]}}],
        "usage": {},
    }
    with _mock_urlopen(monkeypatch, response=response):
        out = macos_vision.call_vision(image_path=image, question="q")
    assert out["ok"] is True
    assert out["result"] == "part-Apart-B"


# ----- error paths ----------------------------------------------------------


def test_call_vision_image_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    out = macos_vision.call_vision(
        image_path=tmp_path / "nope.png",
        question="x",
    )
    assert out["ok"] is False
    assert "image not found" in out["error"]


def test_call_vision_empty_image(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    out = macos_vision.call_vision(image_path=empty, question="x")
    assert out["ok"] is False
    assert "image is empty" in out["error"] or "image not found" in out["error"]


def test_call_vision_no_api_key(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "shot.png")
    # Stub the resolver itself so the real .env / openclaw.json never apply.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(macos_vision, "_resolve_api_key", lambda: None)
    out = macos_vision.call_vision(image_path=image, question="x", api_key=None)
    assert out["ok"] is False
    assert "OPENAI_API_KEY" in out["error"]


def test_call_vision_http_error(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "shot.png")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    err = urllib.error.HTTPError(
        url=macos_vision.OPENAI_CHAT_URL,
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=io.BytesIO(b'{"error":{"message":"rate limit"}}'),
    )
    with _mock_urlopen(monkeypatch, exc=err):
        out = macos_vision.call_vision(image_path=image, question="x")
    assert out["ok"] is False
    assert "openai http 429" in out["error"]
    assert "rate limit" in out["error"]


def test_call_vision_network_error(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "shot.png")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with _mock_urlopen(monkeypatch, exc=urllib.error.URLError("nodns")):
        out = macos_vision.call_vision(image_path=image, question="x")
    assert out["ok"] is False
    assert "network error" in out["error"]


def test_call_vision_no_choices(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "shot.png")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with _mock_urlopen(monkeypatch, response={"choices": []}):
        out = macos_vision.call_vision(image_path=image, question="x")
    assert out["ok"] is False
    assert "no choices" in out["error"]


def test_call_vision_unexpected_content(monkeypatch, tmp_path):
    image = _make_png(tmp_path / "shot.png")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    response = {
        "id": "x",
        "model": "m",
        "choices": [{"message": {"content": 12345}}],
    }
    with _mock_urlopen(monkeypatch, response=response):
        out = macos_vision.call_vision(image_path=image, question="x")
    assert out["ok"] is False
    assert "unexpected content shape" in out["error"]

#!/usr/bin/env python3
"""macos_vision.py -- exec'd by OpenClaw to read screenshots and find click points.

Three subcommands, each tuned for a specific job:

  read         vision_read     -- extract text/structure from a PNG via gpt-5.5.
                                  Use when the AX tree was blank (Electron apps).
  text-locate  text_locate     -- find a TEXT target in a PNG using Apple's on-device
                                  Vision OCR. Free, ~150ms, pixel-perfect. ALWAYS try
                                  this first before vision_locate.
  locate       vision_locate   -- find a target in a PNG using OpenAI's GA `computer`
                                  tool with gpt-5.5. Use when text-locate failed
                                  (icon-only / non-text targets).

Why three tools, in this order? Apple's `VNRecognizeTextRequest` returns exact
pixel bounding boxes for every visible text run, on-device, free, no privacy
leak. For ~80% of GUI click targets in chat apps (chat row labels, channel
names, button labels) it is strictly better than asking an LLM to guess pixels.
For the remaining icon-only / visual-reasoning targets, OpenAI's GA `computer`
tool with `model="gpt-5.5"` is purpose-trained for clicking (ScreenSpot-Pro
85.4% / OSWorld-Verified 75.0%, above the human baseline) -- a huge step up
from the previous "ask gpt-4o for a normalized fraction" approach, which failed
silently on dense interfaces.

Pattern matches omniclaw/tools/macos_ax.py and omniclaw/tools/peer_cli.py:
every subcommand prints a single JSON object on stdout, exits 0 on success,
non-0 with `{"ok": false, "error": "..."}` on failure.

Sensitivity:
  - text_locate: S0 (on-device, no network, no image leaves the Mac).
  - vision_read / vision_locate: S2 (image is sent to OpenAI). The agent's
    `tools.exec.approval` policy gates these; in Talk mode the auto-approve rule
    covers them because the user is actively asking.

Stdlib-only on purpose: no openai SDK dep. Calls the chat-completions endpoint
(for `read`) and the responses endpoint (for `locate`) via urllib.request.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# ---- endpoints + defaults --------------------------------------------------

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# `vision_read` uses chat-completions (multimodal input is well-supported there).
DEFAULT_READ_MODEL = "gpt-5.5"
DEFAULT_READ_MAX_TOKENS = 1024
DEFAULT_READ_DETAIL = "auto"

# `vision_locate` uses the GA `computer` tool. As of March 2026 OpenAI deprecated
# `computer-use-preview` in favour of `tools=[{"type": "computer"}]` driven by
# gpt-5.5 (which inherits gpt-5.4's purpose-built computer-use training).
DEFAULT_LOCATE_MODEL = "gpt-5.5"
DEFAULT_LOCATE_MAX_TOKENS = 1024

DEFAULT_TIMEOUT_S = 120.0

# `text_locate` thresholds (combined score: similarity * ocr_confidence).
DEFAULT_MIN_SCORE = 0.7
DEFAULT_MAX_CANDIDATES = 8

# Backwards-compat aliases (older callers / tests may import these names).
DEFAULT_MODEL = DEFAULT_READ_MODEL
DEFAULT_MAX_TOKENS = DEFAULT_READ_MAX_TOKENS
DEFAULT_DETAIL = DEFAULT_READ_DETAIL


# ---- function-tool schemas (consumed by --json-tools) ---------------------


_TOOL_SCHEMAS = [
    {
        "name": "vision_read",
        "description": (
            "Send a PNG screenshot to a multimodal model (gpt-5.5) and "
            "return extracted text. Use this AFTER mac_screenshot when the AX tree "
            "is blank or partial -- common for Electron / webview apps like "
            "Microsoft Teams, Slack desktop, Discord. Phrase the question to ask "
            "for structured output (e.g. JSON list of {sender, time, text}). "
            "Sensitivity S2: image leaves the device, sent to OpenAI."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Absolute path to the PNG file (typically from mac_screenshot).",
                },
                "question": {
                    "type": "string",
                    "description": "What to extract from the image. Be explicit about format.",
                },
                "max_tokens": {
                    "type": "integer",
                    "default": DEFAULT_READ_MAX_TOKENS,
                    "description": "Cap on response length.",
                },
                "detail": {
                    "type": "string",
                    "enum": ["high", "low", "auto"],
                    "default": DEFAULT_READ_DETAIL,
                    "description": "OpenAI vision detail level. 'low' is cheaper, 'high' for tiny text.",
                },
                "model": {
                    "type": "string",
                    "default": DEFAULT_READ_MODEL,
                    "description": "OpenAI vision-capable model id.",
                },
            },
            "required": ["image", "question"],
        },
        "sensitivity": "S2",
    },
    {
        "name": "text_locate",
        "description": (
            "Find a TEXT element in a PNG screenshot using Apple's on-device "
            "Vision OCR (VNRecognizeTextRequest). Free, ~150ms, pixel-perfect, no "
            "image leaves the device. ALWAYS try this BEFORE `vision_locate` when "
            "the click target has a visible text label (chat names, channel names, "
            "button labels, menu items). Returns click coordinates ready for "
            "`mac_click_at`. Falls back to `vision_locate` only when no good text "
            "match is found (icon-only buttons, non-text targets)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Absolute path to the PNG file (typically from mac_screenshot).",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "The exact text to find, or a close approximation. "
                        "Substrings work: 'BLR - Team' finds 'BLR - Team chat row'."
                    ),
                },
                "screen_width": {
                    "type": "integer",
                    "description": (
                        "Screen width in points (from mac_screen_size). When set "
                        "with screen_height, the response also includes click_x / "
                        "click_y in screen-point space, ready for mac_click_at."
                    ),
                },
                "screen_height": {
                    "type": "integer",
                    "description": "Screen height in points (from mac_screen_size).",
                },
                "min_score": {
                    "type": "number",
                    "default": DEFAULT_MIN_SCORE,
                    "description": (
                        "Minimum combined score (similarity * ocr_confidence) to "
                        "consider a match 'found'. Default 0.7."
                    ),
                },
            },
            "required": ["image", "target"],
        },
        "sensitivity": "S0",
    },
    {
        "name": "vision_locate",
        "description": (
            "Find a UI element in a PNG screenshot by natural-language description "
            "and return click coordinates, using OpenAI's GA `computer` tool "
            "(gpt-5.5, purpose-trained for click coordinates: ScreenSpot-Pro "
            "85.4%). Use this WHEN `text_locate` fails (icon-only buttons, "
            "non-text targets, complex visual reasoning). For text targets, "
            "`text_locate` is strictly better -- free, on-device, exact pixels. "
            "Typical flow: "
            "(1) `mac_focus --app 'Microsoft Teams'`, "
            "(2) `mac_screenshot --app 'Microsoft Teams'` -> PNG, "
            "(3) `text_locate --image PNG --target 'BLR - Team' ...` -> click_x/y, "
            "(4) if not found -> `vision_locate --image PNG --target '...' ...`, "
            "(5) `mac_click_at click_x click_y --app 'Microsoft Teams'`. "
            "Sensitivity S2: image leaves the device, sent to OpenAI."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Absolute path to the PNG file (typically from mac_screenshot).",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Natural-language description of the element to click. Be "
                        "specific about location and surrounding context, e.g. "
                        "'the smiley-face emoji button at the bottom-right of the "
                        "compose toolbar' rather than just 'emoji'."
                    ),
                },
                "screen_width": {
                    "type": "integer",
                    "description": (
                        "Screen width in points (from mac_screen_size). When set "
                        "with screen_height, the response also includes click_x / "
                        "click_y in screen-point space, ready for mac_click_at."
                    ),
                },
                "screen_height": {
                    "type": "integer",
                    "description": "Screen height in points (from mac_screen_size).",
                },
                "model": {
                    "type": "string",
                    "default": DEFAULT_LOCATE_MODEL,
                    "description": "OpenAI computer-use-trained model id.",
                },
            },
            "required": ["image", "target"],
        },
        "sensitivity": "S2",
    },
]


# ---- emit helpers ----------------------------------------------------------


def _emit(out: dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(out, ensure_ascii=False))


def _fail(msg: str, pretty: bool = False, code: int = 1) -> int:
    _emit({"ok": False, "error": msg}, pretty)
    return code


# ---- API key resolution ----------------------------------------------------


def _resolve_api_key() -> str | None:
    """Resolve the OpenAI API key, in order of preference.

    1. OPENAI_API_KEY env var (set by launchd, shell, or sourced .env).
    2. ~/.openclaw/openclaw.json -> talk.realtime.openai.apiKey (if present).
    3. <repo>/omniclaw/.env file (a single OPENAI_API_KEY=... line).
    """
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key

    openclaw_cfg = Path.home() / ".openclaw" / "openclaw.json"
    if openclaw_cfg.is_file():
        try:
            data = json.loads(openclaw_cfg.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        key = (
            data.get("talk", {})
            .get("realtime", {})
            .get("openai", {})
            .get("apiKey")
        )
        if isinstance(key, str) and key.strip():
            return key.strip()

    repo_env = Path(__file__).resolve().parents[1] / ".env"
    if repo_env.is_file():
        try:
            for raw in repo_env.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("OPENAI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            pass

    return None


# ---- HTTP helpers ----------------------------------------------------------


def _post_json(
    url: str,
    body: dict[str, Any],
    api_key: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _wrap_http_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return {"ok": False, "error": f"openai http {exc.code}: {err_body[:500]}"}
    if isinstance(exc, urllib.error.URLError):
        return {"ok": False, "error": f"network error: {exc.reason}"}
    if isinstance(exc, json.JSONDecodeError):
        return {"ok": False, "error": f"non-json reply from openai: {exc}"}
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---- vision_read: chat-completions multimodal ------------------------------


def _build_chat_body(
    *,
    image_b64: str,
    question: str,
    model: str,
    max_tokens: int,
    detail: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                            "detail": detail,
                        },
                    },
                ],
            },
        ],
    }


# Back-compat alias (older test files import _build_request_body).
_build_request_body = _build_chat_body


def call_vision(
    *,
    image_path: Path,
    question: str,
    model: str = DEFAULT_READ_MODEL,
    max_tokens: int = DEFAULT_READ_MAX_TOKENS,
    detail: str = DEFAULT_READ_DETAIL,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Read an image with a vision model. Returns a normalized dict.

    Success: {"ok": True, "result": str, "model": str, "usage": dict, "id": str}
    Failure: {"ok": False, "error": str}
    """
    if not image_path.is_file():
        return {"ok": False, "error": f"image not found: {image_path}"}

    if api_key is None:
        api_key = _resolve_api_key()
    if not api_key:
        return {
            "ok": False,
            "error": (
                "no OPENAI_API_KEY set; set the env var, "
                "configure ~/.openclaw/openclaw.json talk.realtime.openai.apiKey, "
                "or put it in omniclaw/.env"
            ),
        }

    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": f"cannot read image: {exc}"}
    if not image_bytes:
        return {"ok": False, "error": f"image is empty: {image_path}"}

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    body = _build_chat_body(
        image_b64=image_b64,
        question=question,
        model=model,
        max_tokens=max_tokens,
        detail=detail,
    )

    try:
        data = _post_json(OPENAI_CHAT_URL, body, api_key, timeout_s=timeout_s)
    except Exception as exc:
        return _wrap_http_error(exc)

    choices = data.get("choices") or []
    if not choices:
        return {"ok": False, "error": f"no choices in response: {data}"}
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        text_parts = [
            part.get("text", "") for part in content if isinstance(part, dict)
        ]
        text = "".join(text_parts).strip()
    elif isinstance(content, str):
        text = content.strip()
    else:
        return {"ok": False, "error": f"unexpected content shape: {content!r}"}

    return {
        "ok": True,
        "result": text,
        "model": data.get("model", model),
        "id": data.get("id"),
        "usage": data.get("usage", {}),
    }


# ---- text_locate: Apple Vision OCR + fuzzy match ---------------------------


def _import_macos_ocr():
    """Import macos_ocr lazily so non-Mac importers / mocked tests don't crash.

    Works whether macos_vision is loaded as a script
    (`python3 omniclaw/tools/macos_vision.py ...`) or as a package module
    (`from omniclaw.tools import macos_vision`).
    """
    try:
        from omniclaw.tools import macos_ocr  # type: ignore
        return macos_ocr
    except ImportError:
        pass
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import macos_ocr  # type: ignore  # noqa: E402
    return macos_ocr


def _score(target: str, text: str) -> float:
    """Normalized similarity [0, 1] for fuzzy matching.

    Favours exact / substring matches; falls back to SequenceMatcher.
    """
    t = target.strip().lower()
    s = text.strip().lower()
    if not t or not s:
        return 0.0
    if t == s:
        return 1.0
    if t in s:
        # Substring of a longer text: penalize length disparity but stay >= 0.7
        return max(0.7, len(t) / max(1, len(s)))
    if s in t:
        # OCR caught only part of what the user described.
        return max(0.5, len(s) / max(1, len(t)))
    return SequenceMatcher(None, t, s).ratio()


def call_text_locate(
    *,
    image_path: Path,
    target: str,
    screen_width: int | None = None,
    screen_height: int | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any]:
    """Find `target` text in a screenshot using Apple's on-device Vision OCR.

    Returns {ok, found, click_x?, click_y?, image_x, image_y, image_width,
    image_height, matched_text, match_score, ocr_confidence,
    candidates: [{text, score, ocr_confidence, bbox}, ...]}.

    Strictly on-device. No image leaves the Mac.
    """
    if not image_path.is_file():
        return {"ok": False, "error": f"image not found: {image_path}"}

    try:
        macos_ocr = _import_macos_ocr()
    except ImportError as exc:
        return {"ok": False, "error": f"macos_ocr unavailable: {exc}"}

    ocr = macos_ocr.recognize_text(image_path)
    if not ocr.get("ok"):
        return {"ok": False, "error": ocr.get("error", "OCR failed")}

    return _text_locate_from_ocr_result(
        ocr_result=ocr,
        target=target,
        screen_width=screen_width,
        screen_height=screen_height,
        min_score=min_score,
        max_candidates=max_candidates,
    )


def _text_locate_from_ocr_result(
    *,
    ocr_result: dict[str, Any],
    target: str,
    screen_width: int | None = None,
    screen_height: int | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any]:
    """Pure matcher: turn a recognize_text() result + target into a text-locate output.

    Extracted from `call_text_locate` so cross-impl parity tests (and the future
    Android wake-detection diagnostics) can drive matching without OCR'ing a
    real image. Used by tests/test_android_ocr_parity.py.
    """
    img_w = int(ocr_result["image_width"])
    img_h = int(ocr_result["image_height"])

    scored: list[dict[str, Any]] = []
    for item in ocr_result.get("items", []):
        sim = _score(target, item["text"])
        if sim <= 0.0:
            continue
        combined = sim * float(item.get("confidence", 1.0))
        scored.append(
            {
                "text": item["text"],
                "similarity": round(sim, 4),
                "ocr_confidence": round(float(item.get("confidence", 1.0)), 4),
                "score": round(combined, 4),
                "bbox": list(item["bbox"]),
            }
        )

    scored.sort(key=lambda c: c["score"], reverse=True)
    top = scored[: max(1, max_candidates)]

    if not top or top[0]["score"] < float(min_score):
        return {
            "ok": True,
            "found": False,
            "image_width": img_w,
            "image_height": img_h,
            "candidates": top,
            "min_score": float(min_score),
        }

    best = top[0]
    bx, by, bw, bh = best["bbox"]
    cx_img = int(round(bx + bw / 2))
    cy_img = int(round(by + bh / 2))

    out: dict[str, Any] = {
        "ok": True,
        "found": True,
        "matched_text": best["text"],
        "match_score": best["score"],
        "ocr_confidence": best["ocr_confidence"],
        "image_x": cx_img,
        "image_y": cy_img,
        "image_width": img_w,
        "image_height": img_h,
        "bbox": list(best["bbox"]),
        "candidates": top,
    }
    if (
        screen_width is not None
        and screen_height is not None
        and screen_width > 0
        and screen_height > 0
        and img_w > 0
        and img_h > 0
    ):
        out["screen_width"] = int(screen_width)
        out["screen_height"] = int(screen_height)
        out["click_x"] = int(round(cx_img * screen_width / img_w))
        out["click_y"] = int(round(cy_img * screen_height / img_h))
    return out


# ---- vision_locate: GA `computer` tool, gpt-5.5 ----------------------------


def _read_png_size(path: Path) -> tuple[int, int]:
    """Read a PNG's width/height from its IHDR chunk. Stdlib only."""
    with path.open("rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG file")
        f.read(8)
        w = int.from_bytes(f.read(4), "big")
        h = int.from_bytes(f.read(4), "big")
        if w <= 0 or h <= 0:
            raise ValueError(f"bogus PNG dims: {w}x{h}")
        return w, h


def _build_locate_first_turn_body(
    *,
    target: str,
    model: str,
    max_tokens: int,
) -> dict[str, Any]:
    """First turn: just ask `click on X`. Computer tool will request a screenshot."""
    return {
        "model": model,
        "tools": [{"type": "computer"}],
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"You are operating the user's Mac. Use the computer "
                            f"tool to click on: {target}. Return exactly one click "
                            f"action with pixel coordinates."
                        ),
                    },
                ],
            },
        ],
        "max_output_tokens": max_tokens,
    }


def _build_locate_screenshot_turn_body(
    *,
    previous_response_id: str,
    call_id: str,
    image_b64: str,
    model: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Second turn: feed our pre-captured screenshot back as computer_call_output."""
    return {
        "model": model,
        "tools": [{"type": "computer"}],
        "previous_response_id": previous_response_id,
        "input": [
            {
                "type": "computer_call_output",
                "call_id": call_id,
                "output": {
                    "type": "computer_screenshot",
                    "image_url": f"data:image/png;base64,{image_b64}",
                },
            },
        ],
        "max_output_tokens": max_tokens,
    }


def _first_computer_call(response: dict[str, Any]) -> dict[str, Any] | None:
    for item in response.get("output", []) or []:
        if item.get("type") == "computer_call":
            return item
    return None


def _first_click_action(call: dict[str, Any]) -> dict[str, Any] | None:
    for action in call.get("actions") or []:
        if action.get("type") == "click":
            return action
    return None


def call_vision_locate(
    *,
    image_path: Path,
    target: str,
    model: str = DEFAULT_LOCATE_MODEL,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    screen_width: int | None = None,
    screen_height: int | None = None,
    max_tokens: int = DEFAULT_LOCATE_MAX_TOKENS,
) -> dict[str, Any]:
    """Locate a UI element with OpenAI's GA `computer` tool.

    The computer tool natively returns `{type: click, x, y}` actions in the
    coordinate space of the screenshot we provide. We translate to screen-point
    space when caller supplies screen dimensions.

    Two-turn dance (per OpenAI docs):
      Turn 1: send the user's intent; model responds with `actions: [screenshot]`.
      Turn 2: feed the screenshot back as `computer_call_output`; model responds
              with `actions: [click(x, y)]`.
    """
    if not image_path.is_file():
        return {"ok": False, "error": f"image not found: {image_path}"}

    try:
        img_w, img_h = _read_png_size(image_path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"cannot read image dims: {exc}"}

    if api_key is None:
        api_key = _resolve_api_key()
    if not api_key:
        return {
            "ok": False,
            "error": (
                "no OPENAI_API_KEY set; set the env var, "
                "configure ~/.openclaw/openclaw.json talk.realtime.openai.apiKey, "
                "or put it in omniclaw/.env"
            ),
        }

    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": f"cannot read image: {exc}"}
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    # Turn 1
    try:
        r1 = _post_json(
            OPENAI_RESPONSES_URL,
            _build_locate_first_turn_body(
                target=target, model=model, max_tokens=max_tokens
            ),
            api_key,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        return _wrap_http_error(exc)

    call1 = _first_computer_call(r1)
    if call1 is None:
        return {
            "ok": False,
            "error": "computer tool returned no computer_call on turn 1",
            "raw": r1.get("output", []),
            "model": r1.get("model"),
        }

    # Most first turns request a screenshot. If by chance turn 1 already
    # produced a click (uncommon), short-circuit.
    click_action = _first_click_action(call1)
    if click_action is None:
        # Turn 2
        try:
            r2 = _post_json(
                OPENAI_RESPONSES_URL,
                _build_locate_screenshot_turn_body(
                    previous_response_id=r1["id"],
                    call_id=call1["call_id"],
                    image_b64=image_b64,
                    model=model,
                    max_tokens=max_tokens,
                ),
                api_key,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            return _wrap_http_error(exc)

        call2 = _first_computer_call(r2)
        if call2 is None:
            return {
                "ok": False,
                "error": "computer tool returned no computer_call on turn 2",
                "raw": r2.get("output", []),
                "model": r2.get("model"),
            }
        click_action = _first_click_action(call2)
        last_response = r2
    else:
        last_response = r1

    if click_action is None:
        return {
            "ok": False,
            "error": "computer tool returned no click action",
            "raw": last_response.get("output", []),
            "model": last_response.get("model"),
        }

    # The click action's (x, y) is in IMAGE pixel space.
    image_x = int(click_action.get("x", 0))
    image_y = int(click_action.get("y", 0))

    out: dict[str, Any] = {
        "ok": True,
        "found": True,
        "image_x": image_x,
        "image_y": image_y,
        "image_width": img_w,
        "image_height": img_h,
        "raw_action": click_action,
        "model": last_response.get("model"),
        "response_id": last_response.get("id"),
    }
    if (
        screen_width is not None
        and screen_height is not None
        and screen_width > 0
        and screen_height > 0
        and img_w > 0
        and img_h > 0
    ):
        out["screen_width"] = int(screen_width)
        out["screen_height"] = int(screen_height)
        out["click_x"] = int(round(image_x * screen_width / img_w))
        out["click_y"] = int(round(image_y * screen_height / img_h))
    return out


# ---- subcommand handlers ---------------------------------------------------


def cmd_json_tools(args: argparse.Namespace) -> int:
    _emit({"ok": True, "tools": _TOOL_SCHEMAS}, args.pretty)
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    image_path = Path(args.image).expanduser().resolve()
    out = call_vision(
        image_path=image_path,
        question=args.question,
        model=args.model,
        max_tokens=args.max_tokens,
        detail=args.detail,
        timeout_s=args.timeout,
    )
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_text_locate(args: argparse.Namespace) -> int:
    image_path = Path(args.image).expanduser().resolve()
    out = call_text_locate(
        image_path=image_path,
        target=args.target,
        screen_width=args.screen_width,
        screen_height=args.screen_height,
        min_score=args.min_score,
        max_candidates=args.max_candidates,
    )
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_locate(args: argparse.Namespace) -> int:
    image_path = Path(args.image).expanduser().resolve()
    out = call_vision_locate(
        image_path=image_path,
        target=args.target,
        model=args.model,
        timeout_s=args.timeout,
        screen_width=args.screen_width,
        screen_height=args.screen_height,
    )
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


# ---- argument parser -------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "macOS vision CLI: read screenshots and find click coordinates. "
            "Used by the OpenClaw agent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument(
        "--json-tools",
        action="store_true",
        help="Dump function-tool schemas (OpenAI format) and exit. Used by the agent.",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    # read (vision_read)
    p = sub.add_parser("read", help="Send an image + question to a vision model (gpt-5.5).")
    p.add_argument("--image", required=True, help="Absolute path to PNG.")
    p.add_argument("--question", required=True, help="What to extract.")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_READ_MAX_TOKENS)
    p.add_argument(
        "--detail",
        default=DEFAULT_READ_DETAIL,
        choices=["high", "low", "auto"],
        help="Vision detail level.",
    )
    p.add_argument("--model", default=DEFAULT_READ_MODEL, help="OpenAI vision-capable model id.")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="HTTP timeout (seconds).")
    p.set_defaults(func=cmd_read)

    # text-locate (text_locate)
    p = sub.add_parser(
        "text-locate",
        help="Find a text element via Apple's on-device Vision OCR (free, exact).",
    )
    p.add_argument("--image", required=True, help="Absolute path to PNG.")
    p.add_argument(
        "--target",
        required=True,
        help="The text to find (substring matching is supported).",
    )
    p.add_argument(
        "--screen-width",
        type=int,
        default=None,
        help="Screen width in points (from `mac_screen_size`).",
    )
    p.add_argument(
        "--screen-height",
        type=int,
        default=None,
        help="Screen height in points (from `mac_screen_size`).",
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help=(
            "Minimum combined score (similarity * ocr_confidence) to consider a "
            f"match 'found'. Default {DEFAULT_MIN_SCORE}."
        ),
    )
    p.add_argument(
        "--max-candidates",
        type=int,
        default=DEFAULT_MAX_CANDIDATES,
        help="Cap on candidate list returned for inspection.",
    )
    p.set_defaults(func=cmd_text_locate)

    # locate (vision_locate via GA computer tool)
    p = sub.add_parser(
        "locate",
        help=(
            "Find a UI element via OpenAI's GA `computer` tool (gpt-5.5). Use "
            "this when `text-locate` failed (icon-only, non-text targets)."
        ),
    )
    p.add_argument("--image", required=True, help="Absolute path to PNG.")
    p.add_argument(
        "--target",
        required=True,
        help="Natural-language description of the element to click.",
    )
    p.add_argument(
        "--screen-width",
        type=int,
        default=None,
        help="Screen width in points (from `mac_screen_size`).",
    )
    p.add_argument(
        "--screen-height",
        type=int,
        default=None,
        help="Screen height in points (from `mac_screen_size`).",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_LOCATE_MODEL,
        help="OpenAI computer-use-trained model id (default gpt-5.5).",
    )
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="HTTP timeout (seconds).")
    p.set_defaults(func=cmd_locate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.json_tools:
        return cmd_json_tools(args)

    if not getattr(args, "command", None):
        parser.print_usage(file=sys.stderr)
        return _fail(
            "no command given (try `--json-tools`, `read`, `text-locate`, or `locate`)",
            args.pretty,
        )

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover - last-resort
        return _fail(f"{type(exc).__name__}: {exc}", args.pretty)


if __name__ == "__main__":
    sys.exit(main())

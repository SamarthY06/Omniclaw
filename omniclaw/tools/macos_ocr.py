"""macos_ocr.py -- thin wrapper around Apple's Vision framework OCR.

Used by macos_vision.py's `text_locate` subcommand to find click coordinates
for text targets WITHOUT calling out to a remote LLM. Apple's
`VNRecognizeTextRequest` is free, on-device, ~50-200ms per screenshot, and
returns pixel-perfect bounding boxes for every visible text run. For ~80% of
GUI click targets in chat apps (chat row labels, channel names, button
labels), this is the right primitive. The LLM stays for the long-tail
(icons-only buttons, complex visual reasoning).

Public surface:

    recognize_text(
        image_path: Path,
        languages: Sequence[str] = ("en-US",),
        recognition_level: str = "accurate",
        uses_language_correction: bool = True,
    ) -> dict

The returned dict is `{"ok": True, "image_width": int, "image_height": int,
"items": [TextItem, ...]}` where each TextItem has
`{"text": str, "confidence": float, "bbox": (x, y, w, h)}` in IMAGE-pixel
space with origin TOP-LEFT (so it composes directly with how PIL / our
screenshot path think about coordinates).

This module is macOS-only. Importing it on another platform raises
ImportError eagerly so callers can decide what to do.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence

if sys.platform != "darwin":  # pragma: no cover - only meaningful on Mac
    raise ImportError("macos_ocr requires macOS (Apple's Vision framework)")

import objc  # noqa: E402  -- after platform check
import Quartz  # noqa: E402
import Vision  # noqa: E402
from Foundation import NSURL  # noqa: E402

_LEVEL_MAP = {
    "accurate": Vision.VNRequestTextRecognitionLevelAccurate,
    "fast": Vision.VNRequestTextRecognitionLevelFast,
}


def _load_cg_image(path: Path) -> tuple[Any, int, int]:
    url = NSURL.fileURLWithPath_(str(path))
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise OSError(f"could not open image: {path}")
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        raise OSError(f"could not decode image: {path}")
    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)
    return cg_image, int(width), int(height)


def recognize_text(
    image_path: Path,
    *,
    languages: Sequence[str] = ("en-US",),
    recognition_level: str = "accurate",
    uses_language_correction: bool = True,
) -> dict[str, Any]:
    """Run Apple's Vision OCR on a PNG/JPEG and return text + bboxes.

    Coordinates in the returned `bbox` are (x, y, w, h) in IMAGE pixels with
    origin TOP-LEFT. (Vision natively uses normalized bottom-left coords; we
    convert for callers.)
    """
    image_path = Path(image_path).expanduser().resolve()
    if not image_path.is_file():
        return {"ok": False, "error": f"image not found: {image_path}"}

    level = _LEVEL_MAP.get(recognition_level, Vision.VNRequestTextRecognitionLevelAccurate)

    with objc.autorelease_pool():
        try:
            cg_image, img_w, img_h = _load_cg_image(image_path)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(level)
        request.setUsesLanguageCorrection_(bool(uses_language_correction))
        if languages:
            request.setRecognitionLanguages_(list(languages))

        success, err = handler.performRequests_error_([request], None)
        if not success:
            msg = "Vision request failed"
            if err is not None:
                try:
                    msg = f"Vision request failed: {err.localizedDescription()}"
                except Exception:  # pragma: no cover - last-resort
                    msg = f"Vision request failed: {err!r}"
            return {"ok": False, "error": msg}

        observations = request.results() or []
        items: list[dict[str, Any]] = []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            top = candidates[0]
            text = str(top.string())
            if not text:
                continue
            confidence = float(top.confidence())
            bb = obs.boundingBox()
            x = bb.origin.x * img_w
            w = bb.size.width * img_w
            h = bb.size.height * img_h
            # Vision uses normalized bottom-left origin; flip to top-left.
            y_top_from_bottom = bb.origin.y + bb.size.height
            y = (1.0 - y_top_from_bottom) * img_h
            items.append(
                {
                    "text": text,
                    "confidence": confidence,
                    "bbox": (
                        int(round(x)),
                        int(round(y)),
                        int(round(w)),
                        int(round(h)),
                    ),
                }
            )

    return {
        "ok": True,
        "image_width": img_w,
        "image_height": img_h,
        "items": items,
    }


if __name__ == "__main__":
    # Tiny CLI for ad-hoc debugging:
    #   python3 -m omniclaw.tools.macos_ocr <image>
    import json

    if len(sys.argv) != 2:
        print("usage: macos_ocr.py <image>", file=sys.stderr)
        sys.exit(2)
    out = recognize_text(Path(sys.argv[1]))
    if out.get("ok"):
        items = out["items"]
        print(f"image: {out['image_width']}x{out['image_height']}, {len(items)} items")
        for it in items[:50]:
            x, y, w, h = it["bbox"]
            print(f"  {it['confidence']:.2f}  ({x:>5},{y:>5}) {w:>4}x{h:<4}  {it['text']!r}")
    else:
        print(json.dumps(out))
        sys.exit(1)

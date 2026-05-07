"""Always-on Mac wake word listener using Apple's Speech.framework.

Architecture (mirrors Android's BenWakewordService.kt):

    LaunchAgent (ai.ben.wakeword.plist)
      -> python3 -m omniclaw.voice.wakeword_mac --phrase "Ben"
        -> AVAudioEngine pipes the default mic to a 16kHz PCM tap
        -> SFSpeechRecognizer with requiresOnDeviceRecognition=True
        -> partial hypotheses fed through WakePhraseMatcher
        -> on match: exec start_voice.sh (the existing OpenClaw realtime entry)

Why on-device only: SFSpeechRecognizer with `requiresOnDeviceRecognition=True`
keeps the audio entirely local (Apple speech model, never leaves the Mac). We
only open OpenAI Realtime AFTER the wake phrase fires.

Why we rotate the recognition task: SFSpeechRecognitionTask has a hard ~1
minute cap (and AVAudioEngine sometimes drops after ~5 minutes of silence).
We cycle the task every WAKEWORD_ROTATE_SEC seconds AND on every settled
final result. The mic stays open the whole time so there's no listening gap.

No network. No third-party keyword spotter (no Picovoice). No Vosk. Just the
OS recognizer the user already trusts via Siri.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if sys.platform != "darwin":  # pragma: no cover - Mac-only.
    raise ImportError("wakeword_mac requires macOS (uses Apple Speech.framework)")

from omniclaw.voice.wake_phrase_matcher import matches as _matches  # noqa: E402

# Lazy-import PyObjC bits so importing the module on a non-Mac CI box doesn't
# explode, AND so test environments that stub the framework can monkey-patch
# the imports here.
try:  # pragma: no cover - real macOS only.
    import objc  # noqa: F401
    import AVFoundation  # type: ignore  # noqa: E402
    import Speech  # type: ignore  # noqa: E402
    from Foundation import NSObject, NSRunLoop, NSDate, NSError  # type: ignore  # noqa: E402
    HAS_FRAMEWORK = True
except ImportError:
    HAS_FRAMEWORK = False


# Rotate the SFSpeechRecognitionTask before Apple's ~1-minute cap; we settle at
# 50 s to give a comfortable buffer. The mic capture stays running across the
# rotation so the listening window is effectively continuous.
WAKEWORD_ROTATE_SEC = 50.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Always-on wake word listener for Mac.")
    parser.add_argument("--phrase", default=os.environ.get("BEN_WAKE_PHRASE", "Ben"),
                        help="Wake phrase to match (default: Ben).")
    parser.add_argument("--start-script",
                        default=str(Path(__file__).resolve().parent / "start_voice.sh"),
                        help="Path to the script we exec on a wake-phrase match.")
    parser.add_argument("--rearm-after-script", action="store_true", default=True,
                        help="If set (the default), wait for start_voice.sh to exit, "
                             "then resume listening for the next wake event.")
    parser.add_argument("--print-only", action="store_true",
                        help="Print partials to stdout instead of opening the script. "
                             "Useful for diagnostics: `wakeword_mac --print-only`")
    args = parser.parse_args(argv)

    if not HAS_FRAMEWORK:
        print(json.dumps({
            "ok": False,
            "error": ("PyObjC frameworks (AVFoundation, Speech) missing - "
                      "install pyobjc-framework-AVFoundation and pyobjc-framework-Speech"),
        }), flush=True)
        return 2

    listener = WakeListener(
        phrase=args.phrase,
        start_script=args.start_script,
        print_only=args.print_only,
        rearm_after_script=args.rearm_after_script,
    )
    listener.run_forever()
    return 0


class WakeListener:
    """Wraps the AVAudioEngine + SFSpeechRecognizer dance.

    Public surface:
        run_forever() - block forever, dispatch wake events to start_script.
        stop()        - graceful shutdown (SIGTERM handler hooks this).
    """

    def __init__(
        self,
        *,
        phrase: str,
        start_script: str,
        print_only: bool = False,
        rearm_after_script: bool = True,
    ) -> None:
        self.phrase = phrase
        self.start_script = start_script
        self.print_only = print_only
        self.rearm_after_script = rearm_after_script
        self._engine = None
        self._recognizer = None
        self._task = None
        self._request = None
        self._stop_requested = False
        self._task_started_at = 0.0
        self._delegate = None

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        signal.signal(signal.SIGINT, lambda *_: self.stop())

        self._setup_recognizer()
        self._setup_engine_and_tap()
        self._start_task()

        runloop = NSRunLoop.currentRunLoop()
        next_rotate = time.monotonic() + WAKEWORD_ROTATE_SEC
        while not self._stop_requested:
            until = NSDate.dateWithTimeIntervalSinceNow_(0.5)
            runloop.runUntilDate_(until)
            if time.monotonic() >= next_rotate:
                self._rotate_task()
                next_rotate = time.monotonic() + WAKEWORD_ROTATE_SEC

        self._teardown()

    def stop(self) -> None:
        self._stop_requested = True

    def _setup_recognizer(self) -> None:
        self._recognizer = Speech.SFSpeechRecognizer.alloc().init()
        if self._recognizer is None or not self._recognizer.isAvailable():
            raise RuntimeError("SFSpeechRecognizer unavailable")
        if not self._recognizer.supportsOnDeviceRecognition():
            print(json.dumps({
                "warn": "on-device recognition not supported on this Mac/locale; "
                        "the recognizer will refuse to start. Install the offline "
                        "language pack via System Settings -> Accessibility -> Spoken Content.",
            }), flush=True)

    def _setup_engine_and_tap(self) -> None:
        self._engine = AVFoundation.AVAudioEngine.alloc().init()
        input_node = self._engine.inputNode()
        input_format = input_node.outputFormatForBus_(0)

        # We re-create the request each rotation; here we just init it.
        self._request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        self._request.setShouldReportPartialResults_(True)
        self._request.setRequiresOnDeviceRecognition_(True)

        # Tap installs ONCE, then stays across task rotations.
        def _on_buffer(buf, _when):
            try:
                if self._request is not None:
                    self._request.appendAudioPCMBuffer_(buf)
            except Exception as exc:  # pragma: no cover - defensive
                print(json.dumps({"warn": f"audio tap error: {exc}"}), flush=True)
        input_node.installTapOnBus_bufferSize_format_block_(0, 1024, input_format, _on_buffer)

        err = objc.nil  # type: ignore[name-defined]
        ok = self._engine.startAndReturnError_(err)
        if not ok:
            raise RuntimeError("AVAudioEngine.startAndReturnError failed")

    def _start_task(self) -> None:
        if self._recognizer is None or self._request is None:
            return
        self._task_started_at = time.monotonic()

        def _on_result(result, error):  # PyObjC trampoline.
            if error is not None:
                # Common: locale not downloaded, mic muted, etc. Silently rotate.
                return
            if result is None:
                return
            best = result.bestTranscription()
            text = best.formattedString() if best is not None else ""
            self._on_partial(text)

        self._task = self._recognizer.recognitionTaskWithRequest_resultHandler_(
            self._request, _on_result,
        )

    def _rotate_task(self) -> None:
        # End the current request and task, build a fresh pair, reuse the same tap.
        try:
            if self._request is not None:
                self._request.endAudio()
        except Exception:
            pass
        try:
            if self._task is not None:
                self._task.cancel()
        except Exception:
            pass
        self._request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        self._request.setShouldReportPartialResults_(True)
        self._request.setRequiresOnDeviceRecognition_(True)
        self._start_task()

    def _on_partial(self, text: str) -> None:
        if not text:
            return
        if self.print_only:
            print(json.dumps({"partial": text}), flush=True)
            return
        if not _matches(text, self.phrase):
            return
        # Match. Pause our pipeline, run start_voice.sh, then re-arm.
        print(json.dumps({"event": "wake_match", "phrase": self.phrase, "heard": text}), flush=True)
        self._pause_listening()
        try:
            self._exec_start_script()
        finally:
            if self.rearm_after_script:
                self._resume_listening()

    def _exec_start_script(self) -> None:
        if not Path(self.start_script).exists():
            print(json.dumps({"warn": f"start script missing: {self.start_script}"}), flush=True)
            return
        try:
            subprocess.run([self.start_script], check=False)
        except Exception as exc:
            print(json.dumps({"warn": f"start_voice.sh failed: {exc}"}), flush=True)

    def _pause_listening(self) -> None:
        try:
            if self._task is not None:
                self._task.cancel()
        except Exception:
            pass
        try:
            if self._request is not None:
                self._request.endAudio()
        except Exception:
            pass

    def _resume_listening(self) -> None:
        self._request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        self._request.setShouldReportPartialResults_(True)
        self._request.setRequiresOnDeviceRecognition_(True)
        self._start_task()

    def _teardown(self) -> None:
        try:
            if self._task is not None:
                self._task.cancel()
        except Exception:
            pass
        try:
            if self._engine is not None and self._engine.isRunning():
                self._engine.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

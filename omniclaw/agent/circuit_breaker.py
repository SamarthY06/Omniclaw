"""
Circuit Breaker for LLM API calls.
Prevents hammering a failing API in an always-on daemon.
States: CLOSED (normal) → OPEN (failure threshold hit) → HALF_OPEN (testing recovery)
"""

import asyncio
import time
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit is open and calls are blocked."""
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout_sec: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = CircuitState.CLOSED
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(self, coro):
        """Wrap an async coroutine with circuit breaker protection."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout_sec:
                    self._state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker: OPEN → HALF_OPEN (testing recovery)")
                else:
                    remaining = self.recovery_timeout_sec - elapsed
                    raise CircuitOpenError(
                        f"LLM API circuit is open. Retry in {remaining:.0f}s. "
                        "This usually means OpenAI is temporarily unavailable."
                    )

        try:
            result = await coro
            async with self._lock:
                if self._state == CircuitState.HALF_OPEN:
                    logger.info("Circuit breaker: HALF_OPEN → CLOSED (recovery confirmed)")
                self._failure_count = 0
                self._state = CircuitState.CLOSED
            return result

        except CircuitOpenError:
            raise

        except Exception as e:
            async with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.monotonic()
                if self._failure_count >= self.failure_threshold:
                    if self._state != CircuitState.OPEN:
                        logger.error(
                            f"Circuit breaker: CLOSED → OPEN after {self._failure_count} failures. "
                            f"Last error: {e}"
                        )
                    self._state = CircuitState.OPEN
            raise

    def reset(self):
        """Manually reset the circuit breaker (e.g. after config change)."""
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = CircuitState.CLOSED
        logger.info("Circuit breaker manually reset to CLOSED")

    def status(self) -> dict:
        elapsed = time.monotonic() - self._last_failure_time if self._last_failure_time else 0
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "threshold": self.failure_threshold,
            "seconds_since_last_failure": round(elapsed, 1),
            "recovery_timeout_sec": self.recovery_timeout_sec,
        }


# Global singleton — one circuit breaker for all LLM calls on this device
_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        import os
        threshold = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))
        recovery = int(os.getenv("CIRCUIT_BREAKER_RECOVERY_SEC", "60"))
        _circuit_breaker = CircuitBreaker(
            failure_threshold=threshold,
            recovery_timeout_sec=recovery,
        )
    return _circuit_breaker

"""Wake-word: Porcupine on-device detection + UDP arbitration."""

from omniclaw.wake.arbiter import (
    WakeArbiter,
    ArbitrationResult,
    rank_claim,
    MULTICAST_GROUP,
    MULTICAST_PORT,
)

__all__ = [
    "WakeArbiter",
    "ArbitrationResult",
    "rank_claim",
    "MULTICAST_GROUP",
    "MULTICAST_PORT",
]

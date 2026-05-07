"""Pydantic models matching proto/schema.json."""
from __future__ import annotations

import enum
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


SCHEMA_VERSION = 1
SCHEMA_MIN = 1
SCHEMA_MAX = 1


class Sensitivity(str, enum.Enum):
    SAFE = "S0"
    REVERSIBLE = "S1"
    IMPORTANT = "S2"
    SENSITIVE = "S3"


# ---- Envelope --------------------------------------------------------------

class AuthBlock(BaseModel):
    device_id: str
    hmac_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Envelope(BaseModel):
    v: int = SCHEMA_VERSION
    id: str
    kind: Literal["req", "res", "event"]
    method: str
    ts_ms: int
    params: dict[str, Any]
    auth: AuthBlock

    @field_validator("id")
    @classmethod
    def _validate_uuid(cls, v: str) -> str:
        uuid.UUID(v)
        return v

    def signed_dict(self) -> dict[str, Any]:
        """Return the dict the HMAC is computed over (excludes auth.hmac_sha256)."""
        return {
            "v": self.v,
            "id": self.id,
            "kind": self.kind,
            "method": self.method,
            "ts_ms": self.ts_ms,
            "params": self.params,
            "device_id": self.auth.device_id,
        }


# ---- peer.hello -----------------------------------------------------------

class HelloParams(BaseModel):
    schema_version: int = SCHEMA_VERSION
    device_id: str
    role: Literal["mac", "android"]
    caps: list[str]
    pairing_secret: Optional[str] = None


class HelloResult(BaseModel):
    schema_version: int = SCHEMA_VERSION
    device_id: str
    role: Literal["mac", "android"]
    caps: list[str]
    schema_min: int = SCHEMA_MIN
    schema_max: int = SCHEMA_MAX


# ---- peer.ping ------------------------------------------------------------

class PingParams(BaseModel):
    ts_ms: int


class PingResult(BaseModel):
    sent_ts_ms: int
    recv_ts_ms: int
    peer_ts_ms: int


# ---- task.run -------------------------------------------------------------

class TaskRunParams(BaseModel):
    run_id: str
    intent: str
    args: dict[str, Any] = Field(default_factory=dict)
    allow_remote_tools: bool = True
    deadline_ms: int = 60_000


TaskLifecycleStatus = Literal[
    "started", "thinking", "tool_call", "awaiting_user", "completed", "failed", "cancelled"
]


class LifecycleEvent(BaseModel):
    run_id: str
    type: Literal["lifecycle"] = "lifecycle"
    status: TaskLifecycleStatus
    detail: Optional[str] = None


class AssistantEvent(BaseModel):
    run_id: str
    type: Literal["assistant"] = "assistant"
    text_delta: Optional[str] = None
    audio_b64_delta: Optional[str] = None
    final: bool = False


class ToolEvent(BaseModel):
    run_id: str
    type: Literal["tool"] = "tool"
    tool_name: str
    args: dict[str, Any]
    started_at_ms: int
    finished_at_ms: Optional[int] = None
    ok: Optional[bool] = None
    output: Any = None
    error: Optional[str] = None


TaskEvent = LifecycleEvent | AssistantEvent | ToolEvent


class TaskResult(BaseModel):
    run_id: str
    status: Literal["completed", "failed", "cancelled"]
    output: Any = None
    error: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None


# ---- task.cancel ----------------------------------------------------------

class TaskCancelParams(BaseModel):
    run_id: str
    reason: str


class TaskCancelResult(BaseModel):
    run_id: str
    cancelled: bool


# ---- tools.invoke ---------------------------------------------------------

class ToolsInvokeParams(BaseModel):
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = None
    deadline_ms: int = 30_000


class ToolsInvokeResult(BaseModel):
    ok: bool
    output: Any = None
    error: Optional[str] = None


# ---- memory.{read,upsert} -------------------------------------------------

MemoryScope = Literal["contacts", "prefs", "flow_signatures"]


class MemoryItem(BaseModel):
    key: str
    value: Any
    version_ts_ms: int


class MemoryReadParams(BaseModel):
    keys: list[str]
    scope: MemoryScope


class MemoryReadResult(BaseModel):
    items: list[MemoryItem]


class MemoryUpsertItem(MemoryItem):
    scope: MemoryScope


class MemoryUpsertParams(BaseModel):
    items: list[MemoryUpsertItem]


class MemoryRejection(BaseModel):
    key: str
    reason: str


class MemoryUpsertResult(BaseModel):
    accepted: int
    rejected: list[MemoryRejection]


# ---- handoff.screen -------------------------------------------------------

HandoffReason = Literal[
    "otp", "payment", "password", "biometric", "permission_grant", "first_time_recipient"
]


class HandoffScreenParams(BaseModel):
    run_id: str
    reason: HandoffReason
    instructions: str
    screenshot_b64: Optional[str] = None


class HandoffScreenResult(BaseModel):
    acknowledged: bool
    user_action_started: bool


# ---- wake.claim (UDP, not WS) --------------------------------------------

class WakeClaim(BaseModel):
    device_id: str
    rms_dbfs: float
    confidence: float
    ts_ms: int
    priority: int
    schema_version: int = SCHEMA_VERSION


# ---- Method dispatch table -----------------------------------------------

METHOD_PARAMS: dict[str, type[BaseModel]] = {
    "peer.hello": HelloParams,
    "peer.ping": PingParams,
    "task.run": TaskRunParams,
    "task.cancel": TaskCancelParams,
    "tools.invoke": ToolsInvokeParams,
    "memory.read": MemoryReadParams,
    "memory.upsert": MemoryUpsertParams,
    "handoff.screen": HandoffScreenParams,
}

METHOD_RESULTS: dict[str, type[BaseModel]] = {
    "peer.hello": HelloResult,
    "peer.ping": PingResult,
    "task.run": TaskResult,
    "task.cancel": TaskCancelResult,
    "tools.invoke": ToolsInvokeResult,
    "memory.read": MemoryReadResult,
    "memory.upsert": MemoryUpsertResult,
    "handoff.screen": HandoffScreenResult,
}

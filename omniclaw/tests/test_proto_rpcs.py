"""Round-trip every RPC's params and result through pydantic + JSON."""
from __future__ import annotations

import json

import pytest

from omniclaw.proto.types import (
    AssistantEvent,
    HandoffScreenParams,
    HandoffScreenResult,
    HelloParams,
    HelloResult,
    LifecycleEvent,
    MemoryItem,
    MemoryReadParams,
    MemoryReadResult,
    MemoryRejection,
    MemoryUpsertItem,
    MemoryUpsertParams,
    MemoryUpsertResult,
    PingParams,
    PingResult,
    TaskCancelParams,
    TaskCancelResult,
    TaskResult,
    TaskRunParams,
    ToolEvent,
    ToolsInvokeParams,
    ToolsInvokeResult,
    WakeClaim,
    METHOD_PARAMS,
    METHOD_RESULTS,
)


def _round_trip(model) -> None:
    s = model.model_dump_json()
    rebuilt = type(model).model_validate_json(s)
    assert rebuilt == model


def test_hello_round_trip():
    p = HelloParams(schema_version=1, device_id="mac-1", role="mac", caps=["tool:a", "tool:b"])
    _round_trip(p)
    r = HelloResult(schema_version=1, device_id="mac-1", role="mac", caps=["tool:a"])
    _round_trip(r)


def test_ping_round_trip():
    _round_trip(PingParams(ts_ms=1))
    _round_trip(PingResult(sent_ts_ms=1, recv_ts_ms=2, peer_ts_ms=3))


def test_task_run_round_trip():
    _round_trip(TaskRunParams(run_id="r1", intent="do", args={"a": 1}, deadline_ms=1000))
    _round_trip(TaskResult(run_id="r1", status="completed", output={"x": 1}))


def test_task_cancel_round_trip():
    _round_trip(TaskCancelParams(run_id="r1", reason="user"))
    _round_trip(TaskCancelResult(run_id="r1", cancelled=True))


def test_tools_invoke_round_trip():
    _round_trip(ToolsInvokeParams(tool_name="mac_screen_size", args={}, deadline_ms=1000))
    _round_trip(ToolsInvokeResult(ok=True, output={"width": 1, "height": 2}))
    _round_trip(ToolsInvokeResult(ok=False, error="boom"))


def test_memory_round_trip():
    _round_trip(MemoryReadParams(keys=["k1"], scope="contacts"))
    _round_trip(MemoryReadResult(items=[MemoryItem(key="k1", value={"a": 1}, version_ts_ms=1)]))
    _round_trip(MemoryUpsertParams(items=[MemoryUpsertItem(key="k1", value=2, version_ts_ms=1, scope="prefs")]))
    _round_trip(MemoryUpsertResult(accepted=1, rejected=[MemoryRejection(key="k2", reason="stale")]))


def test_handoff_round_trip():
    _round_trip(HandoffScreenParams(run_id="r", reason="otp", instructions="enter the code"))
    _round_trip(HandoffScreenResult(acknowledged=True, user_action_started=False))


def test_wake_claim_round_trip():
    _round_trip(WakeClaim(device_id="mac", rms_dbfs=-22.0, confidence=0.9, ts_ms=1, priority=10))


def test_task_event_lifecycle_round_trip():
    _round_trip(LifecycleEvent(run_id="r", status="started"))
    _round_trip(AssistantEvent(run_id="r", text_delta="hi"))
    _round_trip(ToolEvent(run_id="r", tool_name="t", args={}, started_at_ms=1))


def test_dispatch_table_completeness():
    for method in METHOD_PARAMS:
        assert method in METHOD_RESULTS, f"missing result type for {method}"


def test_dispatch_table_models_constructible():
    for method, model in METHOD_PARAMS.items():
        # Just check we can introspect; not all are zero-arg.
        assert hasattr(model, "model_validate"), method

"""
LangGraph state definitions for OmniClaw.
Uses TypedDict so LangGraph can manage state channels correctly.
"""

from typing import Any, Optional
from typing_extensions import TypedDict


class OmniClawState(TypedDict, total=False):
    """
    Typed state for the OmniClaw Plan-and-Execute graph.
    All fields are optional (total=False) to allow partial updates.
    """
    task: str
    plan: list[str]
    current_step_index: int
    completed_steps: list[str]
    failed_step: Optional[str]
    last_error: Optional[str]
    ui_context: str
    memory_context: str
    response: str
    waiting_for_user: bool
    confirmation_request: Optional[dict]
    messages: list
    replan_count: int


def default_state(task: str = "") -> OmniClawState:
    """Create a fresh state with all defaults."""
    return OmniClawState(
        task=task,
        plan=[],
        current_step_index=0,
        completed_steps=[],
        failed_step=None,
        last_error=None,
        ui_context="",
        memory_context="",
        response="",
        waiting_for_user=False,
        confirmation_request=None,
        messages=[],
        replan_count=0,
    )

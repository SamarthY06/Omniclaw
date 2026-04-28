"""
OmniClaw LangGraph Plan-and-Execute graph.
Flow: plan_step → execute_step → (replan | finish)
All LLM calls go through the circuit breaker.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, END

from agent.circuit_breaker import get_circuit_breaker
from agent.memory import (
    init_memory_db,
    search_memory,
    extract_and_save_memories,
    log_session,
)
from agent.prompts import (
    system_prompt,
    planner_prompt,
    replanner_prompt,
    executor_prompt,
    conversation_routing_prompt,
    observation_prompt,
)
from agent.state import OmniClawState, default_state
from tools.macos_accessibility import (
    launch_app,
    focus_app,
    get_ui_tree,
    get_focused_app_ui_tree,
    flatten_ui_tree,
    keyboard_shortcut,
    type_text,
    click_element_by_title,
    scroll,
    navigate_to_url,
    read_text_from_app,
)

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
logger = logging.getLogger(__name__)

MAX_REPLAN_COUNT = 3
CONFIRM_SENSITIVITY = 2  # Level >= this requires confirmation


# ─── LLM Setup ────────────────────────────────────────────────────────────────

def _make_llm(model_env: str, default: str, **kwargs) -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv(model_env, default),
        temperature=0,
        streaming=True,
        max_retries=2,
        timeout=30,
        **kwargs,
    )


model_reasoning = _make_llm("OMNICLAW_MODEL_REASONING", "gpt-5.4")
model_classification = _make_llm("OMNICLAW_MODEL_CLASSIFICATION", "gpt-5.4-mini")

circuit = get_circuit_breaker()


async def _llm_call(model, prompt: str) -> str:
    """Call LLM through circuit breaker. Returns response text."""
    async def _invoke():
        response = await model.ainvoke(prompt)
        return response.content

    return await circuit.call(_invoke())


# ─── Graph Nodes ──────────────────────────────────────────────────────────────

async def plan_step(state: OmniClawState) -> OmniClawState:
    """
    Node 1: Generate a plan for the task.
    Reads UI context and memory in parallel (async gather).
    """
    task = state["task"]
    logger.info(f"[PLAN] Task: {task}")

    # Parallel prefetch: UI tree + memory search
    ui_task = asyncio.to_thread(get_focused_app_ui_tree)
    memory_task = search_memory(task, limit=5)

    ui_tree_raw, memories = await asyncio.gather(ui_task, memory_task)

    ui_context = flatten_ui_tree(ui_tree_raw, max_elements=60)
    memory_context = "\n".join(
        f"[{m['category']}] {m['content']}" for m in memories
    ) or "(no relevant memories)"

    prompt = planner_prompt(task, ui_context, memory_context)
    plan_text = await _llm_call(model_reasoning, prompt)

    logger.info(f"[PLAN] Raw plan:\n{plan_text}")

    steps = _parse_plan(plan_text)
    confirm_info = _parse_confirm_required(plan_text)

    if not steps:
        if "CANNOT_PROCEED" in plan_text:
            reason = plan_text.split("CANNOT_PROCEED:")[-1].strip()
            return {
                **state,
                "plan": [],
                "response": f"I cannot complete this task: {reason}",
                "waiting_for_user": False,
            }

    return {
        **state,
        "plan": steps,
        "current_step_index": 0,
        "completed_steps": [],
        "ui_context": ui_context,
        "memory_context": memory_context,
        "confirmation_request": confirm_info,
        "last_error": None,
        "failed_step": None,
    }


async def execute_step(state: OmniClawState) -> OmniClawState:
    """
    Node 2: Execute the current plan step.
    Asks the LLM which exact AX action to take, then runs it.
    """
    plan = state["plan"]
    idx = state["current_step_index"]

    if idx >= len(plan):
        return {
            **state,
            "response": f"Task completed: {state['task']}",
        }

    step = plan[idx]
    logger.info(f"[EXECUTE] Step {idx + 1}/{len(plan)}: {step}")

    # Check if this step needs confirmation
    confirm = state.get("confirmation_request")
    if confirm and confirm.get("step") == idx + 1:
        return {
            **state,
            "waiting_for_user": True,
            "response": (
                f"I need your confirmation before proceeding.\n"
                f"About to: {confirm.get('description', step)}\n"
                f"Type 'yes' to continue or 'no' to cancel."
            ),
        }

    # Read fresh UI tree for this step
    ui_tree_raw = await asyncio.to_thread(get_focused_app_ui_tree)
    ui_context = flatten_ui_tree(ui_tree_raw, max_elements=60)

    # Ask LLM what exact action to take
    prompt = executor_prompt(step, ui_context)
    action_json_str = await _llm_call(model_classification, prompt)

    try:
        # Strip markdown code blocks if present
        clean = action_json_str.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        action = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.warning(f"[EXECUTE] Failed to parse action JSON: {e}\nRaw: {action_json_str}")
        return {
            **state,
            "failed_step": step,
            "last_error": f"Could not parse action: {e}",
        }

    sensitivity = action.get("sensitivity", 0)
    if sensitivity >= CONFIRM_SENSITIVITY:
        return {
            **state,
            "waiting_for_user": True,
            "response": (
                f"Confirmation needed (sensitivity level {sensitivity}).\n"
                f"About to: {action.get('sensitivity_reason', step)}\n"
                f"Type 'yes' to continue or 'no' to cancel."
            ),
            "confirmation_request": {
                "step": idx + 1,
                "description": action.get("sensitivity_reason", step),
                "action": action,
            },
        }

    # Execute the action
    result = await _execute_action(action, state)

    if result.get("success"):
        completed = state["completed_steps"] + [step]
        new_idx = idx + 1
        logger.info(f"[EXECUTE] Step {idx + 1} ✓")

        if new_idx >= len(plan):
            return {
                **state,
                "completed_steps": completed,
                "current_step_index": new_idx,
                "response": f"Done! Completed: {state['task']}",
            }
        return {
            **state,
            "completed_steps": completed,
            "current_step_index": new_idx,
        }
    else:
        logger.warning(f"[EXECUTE] Step {idx + 1} failed: {result.get('error')}")
        return {
            **state,
            "failed_step": step,
            "last_error": result.get("error", "Unknown error"),
        }


async def _execute_action(action: dict, state: OmniClawState) -> dict:
    """Map an LLM-chosen action to the actual accessibility call."""
    act = action.get("action", "")
    target = action.get("target", "")
    value = action.get("value", "")

    if act == "launch_app":
        return await asyncio.to_thread(launch_app, target or value)

    elif act in ("activate_app", "focus_window"):
        return await asyncio.to_thread(focus_app, target or value)

    elif act == "keyboard_shortcut":
        shortcut = value or target
        return await asyncio.to_thread(keyboard_shortcut, shortcut)

    elif act == "type_text":
        return await asyncio.to_thread(type_text, value or target)

    elif act == "navigate_url":
        url = value or target
        if not url.startswith("http"):
            url = "https://" + url
        return await asyncio.to_thread(navigate_to_url, url)

    elif act == "click":
        # First try: keyboard-based navigation (most reliable on macOS)
        # If target looks like a URL, navigate directly
        if any(x in target.lower() for x in ["http", "www.", ".com", ".org", ".io"]):
            return await asyncio.to_thread(navigate_to_url, target)

        # Second try: AX element click
        app_tree = await asyncio.to_thread(get_focused_app_ui_tree)
        app_name = app_tree.get("app", "")
        if app_name:
            result = await asyncio.to_thread(click_element_by_title, app_name, target)
            if result.get("success"):
                return result

        # Third try: AppleScript click with app name for reliability
        logger.warning(f"[CLICK] Direct AX click failed for '{target}', trying AppleScript")
        from tools.macos_accessibility import _run_applescript as _as
        # Focus the app first
        if app_name:
            await asyncio.to_thread(focus_app, app_name)
            await asyncio.sleep(0.3)
        script = f'tell application "System Events" to click UI element "{target}" of front window of (first application process whose frontmost is true)'
        result = await asyncio.to_thread(_as, script)
        if result.get("success"):
            return result

        # Fourth try: use menu bar (for app-level actions like New Note)
        if value:
            script2 = f'tell application "System Events" to tell (first application process whose frontmost is true) to click menu item "{value}" of menu 1 of menu bar item "{target}" of menu bar 1'
            return await asyncio.to_thread(_as, script2)

        return {"success": False, "error": f"Could not click '{target}'"}

    elif act == "scroll":
        direction = value or target or "down"
        return await asyncio.to_thread(scroll, direction)

    elif act == "read_element":
        app_tree = await asyncio.to_thread(get_focused_app_ui_tree)
        text = flatten_ui_tree(app_tree, max_elements=100)
        return {"success": True, "content": text}

    elif act == "wait":
        await asyncio.sleep(1.5)
        return {"success": True}

    elif act == "not_found":
        return {"success": False, "error": f"Element not found: {target}"}

    else:
        return {"success": False, "error": f"Unknown action: {act}"}


async def replan_step(state: OmniClawState) -> OmniClawState:
    """
    Node 3: Re-plan when a step fails.
    Capped at MAX_REPLAN_COUNT to prevent infinite loops.
    """
    replan_count = state.get("replan_count", 0) + 1
    if replan_count > MAX_REPLAN_COUNT:
        return {
            **state,
            "response": (
                f"I tried {MAX_REPLAN_COUNT} times but couldn't complete the task.\n"
                f"Last error: {state.get('last_error', 'unknown')}\n"
                f"Completed steps so far: {', '.join(state.get('completed_steps', []))}"
            ),
            "replan_count": replan_count,
        }

    logger.info(f"[REPLAN] Attempt {replan_count}")

    ui_tree_raw = await asyncio.to_thread(get_focused_app_ui_tree)
    ui_context = flatten_ui_tree(ui_tree_raw, max_elements=60)

    prompt = replanner_prompt(
        task=state["task"],
        original_plan="\n".join(f"{i+1}. {s}" for i, s in enumerate(state["plan"])),
        completed_steps=state.get("completed_steps", []),
        failed_step=state.get("failed_step", ""),
        error=state.get("last_error", ""),
        ui_context=ui_context,
    )

    plan_text = await _llm_call(model_reasoning, prompt)
    steps = _parse_plan(plan_text)

    if not steps or "CANNOT_PROCEED" in plan_text:
        reason = plan_text.split("CANNOT_PROCEED:")[-1].strip() if "CANNOT_PROCEED" in plan_text else "Unknown reason"
        return {
            **state,
            "response": f"Cannot complete task after replanning: {reason}",
            "replan_count": replan_count,
        }

    return {
        **state,
        "plan": state.get("completed_steps", []) + steps,  # keep completed, add new
        "current_step_index": len(state.get("completed_steps", [])),
        "failed_step": None,
        "last_error": None,
        "replan_count": replan_count,
        "ui_context": ui_context,
    }


async def finalize_step(state: OmniClawState) -> OmniClawState:
    """
    Node 4: Post-task cleanup.
    Saves the session and fires off memory extraction in background.
    """
    task = state["task"]
    response = state.get("response", "Task complete.")

    # Build conversation text for memory extraction
    conversation = f"User: {task}\nAgent: {response}"
    for step in state.get("completed_steps", []):
        conversation += f"\nCompleted: {step}"

    # Fire-and-forget: extract memories in background
    asyncio.create_task(
        extract_and_save_memories(conversation, model_classification, source="task")
    )

    # Fire-and-forget: log session
    asyncio.create_task(log_session(task, response))

    return state


# ─── Routing Logic ────────────────────────────────────────────────────────────

def should_replan(state: OmniClawState) -> Literal["replan", "finalize", "continue"]:
    if state.get("failed_step") and not state.get("waiting_for_user"):
        return "replan"
    if state.get("response") or state.get("waiting_for_user"):
        return "finalize"
    plan = state.get("plan", [])
    idx = state.get("current_step_index", 0)
    if idx >= len(plan):
        return "finalize"
    return "continue"


def after_replan(state: OmniClawState) -> Literal["execute", "finalize"]:
    if state.get("response"):
        return "finalize"
    return "execute"


# ─── Graph Assembly ───────────────────────────────────────────────────────────

def build_graph(checkpointer=None):
    graph = StateGraph(OmniClawState)

    graph.add_node("plan", plan_step)
    graph.add_node("execute", execute_step)
    graph.add_node("replan", replan_step)
    graph.add_node("finalize", finalize_step)

    graph.set_entry_point("plan")

    graph.add_edge("plan", "execute")

    graph.add_conditional_edges(
        "execute",
        should_replan,
        {
            "replan": "replan",
            "finalize": "finalize",
            "continue": "execute",
        },
    )

    graph.add_conditional_edges(
        "replan",
        after_replan,
        {
            "execute": "execute",
            "finalize": "finalize",
        },
    )

    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


# ─── Helper Parsers ───────────────────────────────────────────────────────────

def _parse_plan(text: str) -> list[str]:
    """Extract numbered steps from planner output."""
    steps = []
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^\d+\.\s+(.+)", line)
        if match:
            step = match.group(1).strip()
            if step and not step.startswith("#"):
                steps.append(step)
    return steps


def _parse_confirm_required(text: str) -> dict | None:
    """Extract CONFIRM_REQUIRED block if present."""
    if "CONFIRM_REQUIRED:" not in text:
        return None
    part = text.split("CONFIRM_REQUIRED:")[-1].strip().split("\n")[0]
    match = re.match(r"(\d+)\s*[-–]\s*(.+)", part)
    if match:
        return {
            "step": int(match.group(1)),
            "description": match.group(2).strip(),
        }
    return {"step": 0, "description": part}


# ─── Main async runner ────────────────────────────────────────────────────────

async def run_task(task: str, thread_id: str = "default") -> str:
    """
    Run a single task end-to-end. Returns the agent's response string.
    Used by main.py and test runner.
    """
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        os.getenv("OMNICLAW_CHECKPOINT_DB", "data/checkpoints.db"),
    )
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)

        initial_state = default_state(task=task)

        config = {"configurable": {"thread_id": thread_id}}

        start = time.monotonic()
        final_state = await graph.ainvoke(initial_state, config=config)
        elapsed = time.monotonic() - start

        response = final_state.get("response", "Task complete.")
        logger.info(f"[DONE] Task finished in {elapsed:.1f}s: {response[:100]}")
        return response

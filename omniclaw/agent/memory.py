"""
SQLite-based memory system for OmniClaw.
Stores long-term facts, user preferences, app knowledge, and corrections.
Uses aiosqlite for async access.
Includes correction/reinforcement signal detection (from DeerFlow pattern).
"""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, UTC
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


def _db_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, os.getenv("OMNICLAW_MEMORY_DB", "data/memory.db"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


# ─── Schema ──────────────────────────────────────────────────────────────────

CREATE_FACTS_TABLE = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'context',
    confidence REAL NOT NULL DEFAULT 0.5,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL
);
"""

CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
USING fts5(content, category, tokenize='porter ascii');
"""

CREATE_FTS_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS facts_fts_insert AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, category) VALUES (new.rowid, new.content, new.category);
END;
"""

CREATE_FTS_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS facts_fts_delete AFTER DELETE ON facts BEGIN
    DELETE FROM facts_fts WHERE rowid = old.rowid;
END;
"""


async def init_memory_db():
    """Initialize the memory database and create tables."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(CREATE_FACTS_TABLE)
        await db.execute(CREATE_SESSIONS_TABLE)
        try:
            await db.execute(CREATE_FTS_TABLE)
            await db.execute(CREATE_FTS_TRIGGER_INSERT)
            await db.execute(CREATE_FTS_TRIGGER_DELETE)
        except Exception:
            pass  # FTS5 already exists or not available
        await db.commit()
    logger.info(f"Memory DB initialized at {_db_path()}")


# ─── Fact Storage ─────────────────────────────────────────────────────────────

async def save_fact(
    content: str,
    category: str = "context",
    confidence: float = 0.7,
    source: str = "agent",
) -> str:
    """Save a new fact. Returns the fact id."""
    now = datetime.now(UTC).isoformat()
    fact_id = f"fact_{uuid.uuid4().hex[:8]}"

    async with aiosqlite.connect(_db_path()) as db:
        # Deduplicate: check if near-identical fact exists
        async with db.execute(
            "SELECT id FROM facts WHERE LOWER(content) = LOWER(?)", (content.strip(),)
        ) as cursor:
            existing = await cursor.fetchone()
            if existing:
                logger.debug(f"Skipping duplicate fact: {content[:60]}")
                return existing[0]

        await db.execute(
            "INSERT INTO facts (id, content, category, confidence, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (fact_id, content.strip(), category, confidence, source, now, now),
        )
        await db.commit()

    logger.info(f"Saved fact [{category}] conf={confidence}: {content[:80]}")
    return fact_id


async def search_memory(query: str, limit: int = 5) -> list[dict]:
    """
    Hybrid search: keyword match (FTS5) + confidence-sorted retrieval.
    Returns top facts relevant to the query.
    """
    results = []

    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row

        # Try FTS5 first
        try:
            async with db.execute(
                """SELECT f.id, f.content, f.category, f.confidence, f.source, f.created_at
                   FROM facts f
                   JOIN facts_fts ff ON f.rowid = ff.rowid
                   WHERE facts_fts MATCH ?
                   ORDER BY f.confidence DESC, f.created_at DESC
                   LIMIT ?""",
                (query, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                results = [dict(r) for r in rows]
        except Exception:
            pass

        # If no FTS results, fall back to LIKE search
        if not results:
            terms = query.lower().split()[:3]
            conditions = " OR ".join(["LOWER(content) LIKE ?" for _ in terms])
            params = [f"%{t}%" for t in terms] + [limit]
            async with db.execute(
                f"SELECT id, content, category, confidence, source, created_at FROM facts WHERE {conditions} ORDER BY confidence DESC LIMIT ?",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
                results = [dict(r) for r in rows]

        # Always include high-confidence correction/preference facts
        if len(results) < limit:
            async with db.execute(
                """SELECT id, content, category, confidence, source, created_at FROM facts
                   WHERE category IN ('correction', 'preference') AND confidence >= 0.9
                   ORDER BY confidence DESC LIMIT ?""",
                (limit - len(results),),
            ) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    r_dict = dict(r)
                    if r_dict["id"] not in {x["id"] for x in results}:
                        results.append(r_dict)

    return results[:limit]


async def get_all_facts_summary() -> str:
    """Return a short summary of stored facts for injection into memory extraction prompt."""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT content, category FROM facts ORDER BY confidence DESC, created_at DESC LIMIT 20"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        return "(no stored memories yet)"

    lines = [f"[{r['category']}] {r['content']}" for r in rows]
    return "\n".join(lines)


# ─── Memory Extraction (runs fire-and-forget after task completion) ───────────

async def extract_and_save_memories(
    conversation: str,
    llm,
    source: str = "task",
) -> None:
    """
    Fire-and-forget memory extraction.
    Called via asyncio.create_task() after task completion.
    Uses the nano/mini LLM to extract facts + detect corrections/reinforcements.
    """
    from agent.prompts import memory_extraction_prompt

    try:
        current_summary = await get_all_facts_summary()
        prompt = memory_extraction_prompt(conversation, current_summary)

        response = await llm.ainvoke(prompt)
        text = response.content.strip()

        # Parse JSON response
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data = json.loads(text)
        facts = data.get("facts", [])
        correction_detected = data.get("correction_detected", False)
        reinforcement_detected = data.get("reinforcement_detected", False)

        if correction_detected:
            logger.info("Correction signal detected — storing with high confidence")
        if reinforcement_detected:
            logger.info("Reinforcement signal detected — storing preference")

        saved_count = 0
        for fact in facts:
            if fact.get("confidence", 0) >= 0.7:
                await save_fact(
                    content=fact["content"],
                    category=fact.get("category", "context"),
                    confidence=fact["confidence"],
                    source=source,
                )
                saved_count += 1

        logger.info(f"Memory extraction complete: {saved_count} facts saved")

    except json.JSONDecodeError as e:
        logger.warning(f"Memory extraction: failed to parse LLM response: {e}")
    except Exception as e:
        logger.exception(f"Memory extraction failed: {e}")


# ─── Session Logging ──────────────────────────────────────────────────────────

async def log_session(task: str, result: str) -> None:
    """Log a completed task session for memory extraction context."""
    now = datetime.now(UTC).isoformat()
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO sessions (session_id, task, result, created_at) VALUES (?,?,?,?)",
            (session_id, task, result, now),
        )
        await db.commit()


async def get_recent_sessions(limit: int = 5) -> list[dict]:
    """Get recent task sessions for context."""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT task, result, created_at FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]

"""
OmniClaw automated test runner — complex multi-step scenarios.

Usage:
  python3 tests/run_tests.py                  # run all tests
  python3 tests/run_tests.py --test TC-C01    # run one test
  python3 tests/run_tests.py --sanity         # sanity checks only
  python3 tests/run_tests.py --complex        # complex tests only
  python3 tests/run_tests.py --verbose        # detailed output
"""

import asyncio
import argparse
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

logging.basicConfig(level=logging.WARNING)

from agent.graph import run_task
from agent.memory import init_memory_db


# ─── Sanity Checks ───────────────────────────────────────────────────────────

SANITY_TESTS = [
    {
        "id": "TC-S01",
        "name": "Launch Chrome",
        "command": "Open Google Chrome",
        "expected_keywords": ["chrome", "open", "done", "complet", "launch"],
        "max_seconds": 10,
        "require_app": None,
    },
    {
        "id": "TC-S02",
        "name": "Launch Chrome + Navigate to google.com",
        "command": "Open Google Chrome and go to google.com",
        "expected_keywords": ["chrome", "google", "done", "complet", "navigat"],
        "max_seconds": 20,
        "require_app": "Google Chrome",
    },
]

# ─── Complex Multi-Step Scenarios ─────────────────────────────────────────────

COMPLEX_TESTS = [
    {
        "id": "TC-C01",
        "name": "Multi-App: Chrome weather search -> Notes write-up",
        "command": (
            "Open Chrome, search for the weather in Bangalore, "
            "then open Notes and create a new note that says "
            "'Bangalore weather: <what you found>'"
        ),
        "expected_keywords": [
            "bangalore", "weather", "note", "notes", "done", "complet",
        ],
        "max_seconds": 60,
        "require_app": "Google Chrome",
    },
    {
        "id": "TC-C02",
        "name": "Chained browser: YouTube search + new tab Amazon",
        "command": (
            "Go to YouTube in Chrome, search for 'how to make pasta', "
            "click the first video, then open a new tab and go to amazon.com"
        ),
        "expected_keywords": [
            "youtube", "pasta", "amazon", "video", "tab", "done", "complet",
        ],
        "max_seconds": 60,
        "require_app": "Google Chrome",
    },
    {
        "id": "TC-C03",
        "name": "Research + Summarise: macOS Sequoia features",
        "command": (
            "Search Google for 'latest macOS Sequoia features' and "
            "tell me the 3 most important new features"
        ),
        "expected_keywords": [
            "sequoia", "feature", "macos", "1", "2", "3",
        ],
        "max_seconds": 45,
        "require_app": "Google Chrome",
    },
    {
        "id": "TC-C04",
        "name": "Direct URL + data extraction: GitHub stars",
        "command": (
            "Go to github.com/bytedance/deer-flow in Chrome and "
            "tell me how many stars the repo has"
        ),
        "expected_keywords": [
            "star", "github", "deer", "repo",
        ],
        "max_seconds": 45,
        "require_app": "Google Chrome",
    },
    {
        "id": "TC-C05",
        "name": "System Settings + Chrome search combo",
        "command": (
            "Open System Settings, then open Chrome and "
            "search for 'how to change Mac wallpaper'"
        ),
        "expected_keywords": [
            "system", "settings", "wallpaper", "chrome", "done", "complet",
        ],
        "max_seconds": 45,
        "require_app": None,
    },
    {
        "id": "TC-C06",
        "name": "Sequential search with memory reference",
        "command": (
            "Open Chrome and search for 'best restaurants in Bangalore'. "
            "Remember the top 3 results. "
            "Then search for directions to the first one."
        ),
        "expected_keywords": [
            "restaurant", "bangalore", "direction", "done", "complet",
        ],
        "max_seconds": 60,
        "require_app": "Google Chrome",
    },
    {
        "id": "TC-C07",
        "name": "Error recovery: non-existent URL",
        "command": (
            "Open Chrome, go to http://thissitedoesnotexist12345.com, "
            "and tell me what happened"
        ),
        "expected_keywords": [
            "error", "not", "found", "reach", "dns", "fail", "exist",
            "cannot", "unavailable", "resolve",
        ],
        "max_seconds": 40,
        "require_app": "Google Chrome",
    },
    {
        "id": "TC-C08",
        "name": "Round-trip: Notes -> Chrome price search -> back to Notes",
        "command": (
            "Open Notes, create a new note titled 'Shopping List', "
            "then open Chrome and search for 'iPhone 16 price in India', "
            "then switch back to Notes and add the price you found"
        ),
        "expected_keywords": [
            "note", "shopping", "price", "iphone", "done", "complet",
        ],
        "max_seconds": 75,
        "require_app": "Google Chrome",
    },
    {
        "id": "TC-C09",
        "name": "Graceful failure for missing app",
        "command": "Open an app called 'FakeAppThatDoesNotExist2026'",
        "expected_keywords": [
            "not find", "cannot", "found", "doesn't exist", "unable", "fail",
            "not installed", "could not", "tried", "error", "couldn't",
        ],
        "max_seconds": 20,
        "require_app": None,
    },
]

ALL_TESTS = SANITY_TESTS + COMPLEX_TESTS


# ─── Runner ───────────────────────────────────────────────────────────────────

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def check_app_installed(app_name: str) -> bool:
    import subprocess
    result = subprocess.run(["open", "-Ra", app_name], capture_output=True)
    return result.returncode == 0


async def run_single_test(test: dict, verbose: bool = False) -> dict:
    tid = test["id"]
    name = test["name"]
    command = test["command"]
    expected = [k.lower() for k in test["expected_keywords"]]
    max_sec = test["max_seconds"]
    require_app = test.get("require_app")

    if require_app and not check_app_installed(require_app):
        return {
            "id": tid, "name": name, "status": "SKIP",
            "reason": f"{require_app} not installed",
            "elapsed": 0,
        }

    start = time.monotonic()
    try:
        response = await asyncio.wait_for(
            run_task(command, thread_id=f"test_{tid}"),
            timeout=max_sec + 15,
        )
        elapsed = time.monotonic() - start

        response_lower = response.lower()
        keyword_hits = [k for k in expected if k in response_lower]
        passed = len(keyword_hits) >= 1

        if verbose:
            print(f"\n  Response: {response[:300]}")
            print(f"  Keywords hit: {keyword_hits}")

        return {
            "id": tid,
            "name": name,
            "status": "PASS" if passed else "WARN",
            "elapsed": round(elapsed, 1),
            "response": response[:200],
            "keyword_hits": keyword_hits,
            "over_time": elapsed > max_sec,
        }

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        return {
            "id": tid, "name": name, "status": "FAIL",
            "reason": f"Timed out after {elapsed:.0f}s (limit {max_sec}s)",
            "elapsed": round(elapsed, 1),
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "id": tid, "name": name, "status": "FAIL",
            "reason": str(e)[:200], "elapsed": round(elapsed, 1),
        }


async def run_all_tests(
    filter_id: str = None,
    sanity_only: bool = False,
    complex_only: bool = False,
    verbose: bool = False,
):
    await init_memory_db()

    if filter_id:
        tests_to_run = [t for t in ALL_TESTS if t["id"] == filter_id]
        if not tests_to_run:
            print(f"No test found with id '{filter_id}'")
            return
    elif sanity_only:
        tests_to_run = SANITY_TESTS
    elif complex_only:
        tests_to_run = COMPLEX_TESTS
    else:
        tests_to_run = ALL_TESTS

    C = Colors
    label = "Sanity" if sanity_only else "Complex" if complex_only else "Full Suite"
    print(f"\n{C.BOLD}{'='*65}{C.RESET}")
    print(f"{C.BOLD}  OmniClaw Test Suite — {label}  ({len(tests_to_run)} tests){C.RESET}")
    print(f"{C.BOLD}{'='*65}{C.RESET}\n")

    results = []
    for test in tests_to_run:
        tid = test["id"]
        name = test["name"]
        print(f"{C.CYAN}[{tid}]{C.RESET} {name}...", end=" ", flush=True)

        result = await run_single_test(test, verbose=verbose)
        results.append(result)

        status = result["status"]
        elapsed = result.get("elapsed", 0)

        if status == "PASS":
            tag = f" {C.YELLOW}(slow){C.RESET}" if result.get("over_time") else ""
            print(f"{C.GREEN}PASS{C.RESET} ({elapsed}s){tag}")
        elif status == "SKIP":
            print(f"{C.YELLOW}SKIP{C.RESET} — {result.get('reason', '')}")
        elif status == "WARN":
            print(f"{C.YELLOW}WARN{C.RESET} ({elapsed}s) — no expected keywords matched")
            if verbose:
                print(f"       Response: {result.get('response', '')[:150]}")
        else:
            print(f"{C.RED}FAIL{C.RESET} ({elapsed}s) — {result.get('reason', '')[:120]}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    warned = sum(1 for r in results if r["status"] == "WARN")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    total_time = sum(r.get("elapsed", 0) for r in results)

    print(f"\n{C.BOLD}{'='*65}{C.RESET}")
    print(
        f"  {C.GREEN}{passed} PASS{C.RESET}  "
        f"{C.RED}{failed} FAIL{C.RESET}  "
        f"{C.YELLOW}{warned} WARN  {skipped} SKIP{C.RESET}"
    )
    print(f"  Total time: {total_time:.1f}s")
    print(f"{C.BOLD}{'='*65}{C.RESET}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OmniClaw test runner")
    parser.add_argument("--test", help="Run a specific test by ID (e.g. TC-C01)")
    parser.add_argument("--sanity", action="store_true", help="Run sanity checks only")
    parser.add_argument("--complex", action="store_true", help="Run complex tests only")
    parser.add_argument("--verbose", action="store_true", help="Show full responses")
    args = parser.parse_args()

    asyncio.run(
        run_all_tests(
            filter_id=args.test,
            sanity_only=args.sanity,
            complex_only=args.complex,
            verbose=args.verbose,
        )
    )

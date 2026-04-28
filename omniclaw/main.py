"""
OmniClaw CLI — main entry point.
Run: python3 main.py
Then type your command and press Enter.
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Load .env from the omniclaw/ directory
_BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE, ".env"))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(_BASE, "data", "omniclaw.log")),
    ],
)
logger = logging.getLogger("omniclaw")

# Add omniclaw dir to path
sys.path.insert(0, _BASE)

from agent.graph import run_task
from agent.memory import init_memory_db
from agent.circuit_breaker import get_circuit_breaker


BANNER = """
╔═══════════════════════════════════════════════════════════╗
║              OmniClaw — macOS AI Agent                    ║
║  Type a command and press Enter.                          ║
║  Type 'quit' to exit, 'status' to check circuit breaker.  ║
╚═══════════════════════════════════════════════════════════╝
"""

EXAMPLES = """
Try:
  → "Open Google Chrome and search for what is AI"
  → "Open YouTube and play a Python tutorial"
  → "Open Notes and create a note saying Meeting tomorrow at 3pm"
  → "Open WhatsApp"
  → "Search for latest iPhone price on Google"
"""


async def main():
    os.makedirs(os.path.join(_BASE, "data"), exist_ok=True)

    print(BANNER)
    print(EXAMPLES)

    # Init memory DB
    await init_memory_db()
    logger.info("Memory DB ready")

    circuit = get_circuit_breaker()
    session_count = 0

    while True:
        try:
            command = input("\n🎙  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not command:
            continue

        if command.lower() == "quit":
            print("Goodbye!")
            break

        if command.lower() == "status":
            s = circuit.status()
            print(f"\n📊 Circuit Breaker: {s}")
            continue

        if command.lower() == "memories":
            from agent.memory import search_memory
            facts = await search_memory(command, limit=10)
            if facts:
                print("\n🧠 Stored memories:")
                for f in facts:
                    print(f"  [{f['category']}] {f['content']}")
            else:
                print("\n(no memories stored yet)")
            continue

        session_count += 1
        thread_id = f"session_{session_count}"

        print(f"\n🤖 OmniClaw: Working on it...\n")

        try:
            from agent.circuit_breaker import CircuitOpenError
            response = await run_task(command, thread_id=thread_id)
            print(f"\n✅ OmniClaw: {response}")

        except CircuitOpenError as e:
            print(f"\n⚡ {e}")

        except Exception as e:
            logger.exception(f"Task failed: {e}")
            print(f"\n❌ Something went wrong: {e}")


if __name__ == "__main__":
    asyncio.run(main())

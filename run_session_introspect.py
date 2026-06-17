"""
Stretch S4 — "What have you learned?"

No new documents. No new context. The agent reads its memory store and
summarises everything it knows. This is the purest demo of cross-session
memory: the agent talking back what it chose to retain.

Usage:
    python3 run_session_introspect.py
"""

import os
import time
from pathlib import Path

import httpx
from anthropic import Anthropic

OUTPUT_DIR = Path("outputs")


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    for required in (".agent_id", ".environment_id", ".memory_store_id"):
        if not Path(required).exists():
            raise SystemExit(f"Missing {required}. Run create_agent.py first.")

    agent_id = Path(".agent_id").read_text().strip()
    environment_id = Path(".environment_id").read_text().strip()
    memory_store_id = Path(".memory_store_id").read_text().strip()

    client = Anthropic()

    print(f"Starting introspection session (no new docs)...")
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title="Introspection — what have you learned?",
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": memory_store_id,
                "access": "read_write",
                "instructions": "Read your memory store and report everything you know.",
            }
        ],
    )

    user_message = (
        "I'm not giving you any new documents today.\n\n"
        "Please read your memory store at /mnt/memory/ and then answer:\n\n"
        "> Summarise everything you have learned about this domain across our "
        "previous sessions. Be specific: what policies do you know, who are the "
        "key people, what has changed since you first learned about this domain, "
        "and what would you tell a new joiner right now?\n\n"
        "Reference your memory files directly. If anything in memory is uncertain "
        "or flagged, say so."
    )

    final_text_parts: list[str] = []
    print("\nAgent working...\n")

    for attempt in range(1, 4):
        try:
            with client.beta.sessions.events.stream(session.id) as stream:
                client.beta.sessions.events.send(
                    session.id,
                    events=[
                        {
                            "type": "user.message",
                            "content": [{"type": "text", "text": user_message}],
                        }
                    ],
                )
                for event in stream:
                    if event.type == "agent.message":
                        for block in event.content:
                            if getattr(block, "type", None) == "text":
                                final_text_parts.append(block.text)
                                print(block.text, end="", flush=True)
                    elif event.type == "agent.tool_use":
                        name = getattr(event, "name", "?")
                        inp = getattr(event, "input", {}) or {}
                        target = inp.get("path") or inp.get("file_path") or inp.get("command") or ""
                        if "/mnt/memory" in str(target):
                            print(f"\n  [memory: {name}  {target}]", flush=True)
                        else:
                            print(f"\n  [{name}]", flush=True)
                    elif event.type == "session.status_idle":
                        print("\n\n[agent finished]")
                        break
            break  # success — exit retry loop
        except httpx.ReadError as e:
            if attempt == 3:
                raise
            wait = attempt * 3
            print(f"\n[connection reset — retrying in {wait}s (attempt {attempt}/3)]", flush=True)
            time.sleep(wait)
            # Create a fresh session for the retry
            session = client.beta.sessions.create(
                agent=agent_id,
                environment_id=environment_id,
                title=f"Introspection — what have you learned? (retry {attempt})",
                resources=[
                    {
                        "type": "memory_store",
                        "memory_store_id": memory_store_id,
                        "access": "read_write",
                        "instructions": "Read your memory store and report everything you know.",
                    }
                ],
            )
            final_text_parts.clear()

    final_text = "".join(final_text_parts)
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / "session_introspect.txt"
    out.write_text(f"=== INTROSPECTION SESSION ===\n\n--- MEMORY SUMMARY ---\n{final_text}\n")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

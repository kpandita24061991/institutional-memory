"""
S8 — Memory diff script.

Two modes:

  python3 memory_diff.py snapshot <label>
      Snapshot the current memory store to outputs/snapshots/<label>.json

  python3 memory_diff.py diff <label-before> <label-after>
      Compare two snapshots and print what changed (added / updated / deleted).

Typical workflow:
  python3 memory_diff.py snapshot before-s2
  python3 run_session_2.py
  python3 memory_diff.py snapshot after-s2
  python3 memory_diff.py diff before-s2 after-s2

Usage:
  python3 memory_diff.py snapshot <label>
  python3 memory_diff.py diff <label-before> <label-after>
  python3 memory_diff.py snapshots          # list saved snapshots
"""

import difflib
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic

SNAPSHOTS_DIR = Path("outputs/snapshots")


# ── snapshot ──────────────────────────────────────────────────────────────────

def cmd_snapshot(label: str) -> None:
    store_id = _require_store_id()
    client = Anthropic()

    print(f"Snapshotting memory store → '{label}'...")
    data: dict[str, str] = {}

    page = client.beta.memory_stores.memories.list(
        store_id, path_prefix="/", order_by="path"
    )
    for item in page.data:
        if item.type != "memory":
            continue
        retrieved = client.beta.memory_stores.memories.retrieve(
            item.id, memory_store_id=store_id
        )
        data[item.path] = retrieved.content or ""
        print(f"  captured {item.path}  ({len(data[item.path])} chars)")

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOTS_DIR / f"{label}.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"\nSnapshot saved to {out}  ({len(data)} file(s))")


# ── diff ──────────────────────────────────────────────────────────────────────

def cmd_diff(label_before: str, label_after: str) -> None:
    before = _load_snapshot(label_before)
    after  = _load_snapshot(label_after)

    all_paths = sorted(set(before) | set(after))

    added    = [p for p in all_paths if p not in before]
    deleted  = [p for p in all_paths if p not in after]
    changed  = [p for p in all_paths if p in before and p in after and before[p] != after[p]]
    unchanged = [p for p in all_paths if p in before and p in after and before[p] == after[p]]

    print(f"\n{'='*60}")
    print(f"  MEMORY DIFF:  {label_before}  →  {label_after}")
    print(f"{'='*60}")
    print(f"  {len(added)} added    {len(deleted)} deleted    "
          f"{len(changed)} updated    {len(unchanged)} unchanged\n")

    for path in added:
        print(f"\n{'─'*60}")
        print(f"  ADDED   {path}")
        print(f"{'─'*60}")
        for line in after[path].splitlines():
            print(f"  + {line}")

    for path in deleted:
        print(f"\n{'─'*60}")
        print(f"  DELETED {path}")
        print(f"{'─'*60}")
        for line in before[path].splitlines():
            print(f"  - {line}")

    for path in changed:
        print(f"\n{'─'*60}")
        print(f"  UPDATED {path}")
        print(f"{'─'*60}")
        _print_diff(before[path], after[path])

    # Surface any [UNVERIFIED] blocks that appeared in the after snapshot
    unverified_paths = [
        p for p in after
        if "[UNVERIFIED]" in after[p] or "UNVERIFIED" in after[p]
    ]
    if unverified_paths:
        print(f"\n{'─'*60}")
        print(f"  QUARANTINED CLAIMS  (present in after snapshot)")
        print(f"{'─'*60}")
        for path in unverified_paths:
            in_block = False
            for line in after[path].splitlines():
                if "UNVERIFIED" in line or "Flagged" in line or "flagged" in line:
                    in_block = True
                if in_block:
                    print(f"  ! {line}")
                if in_block and line.strip() == "":
                    # one blank line ends the block
                    in_block = False

    if not added and not deleted and not changed:
        print("  (no changes between snapshots)")

    print(f"\n{'='*60}\n")


def _print_diff(before: str, after: str) -> None:
    before_lines = before.splitlines(keepends=True)
    after_lines  = after.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        before_lines, after_lines,
        lineterm="",
        n=2,
    ))
    if not diff:
        print("  (binary-identical — metadata change only)")
        return
    for line in diff[2:]:  # skip the --- / +++ header lines
        if line.startswith("+"):
            print(f"  + {line[1:]}")
        elif line.startswith("-"):
            print(f"  - {line[1:]}")
        elif line.startswith("@@"):
            print(f"\n  {line}")
        else:
            print(f"    {line}")


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list() -> None:
    if not SNAPSHOTS_DIR.exists():
        print("No snapshots yet. Run:  python3 memory_diff.py snapshot <label>")
        return
    snaps = sorted(SNAPSHOTS_DIR.glob("*.json"))
    if not snaps:
        print("No snapshots yet.")
        return
    print("Saved snapshots:")
    for s in snaps:
        data = json.loads(s.read_text())
        print(f"  {s.stem:<30}  ({len(data)} file(s))")


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_store_id() -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")
    p = Path(".memory_store_id")
    if not p.exists():
        raise SystemExit("Missing .memory_store_id. Run create_agent.py first.")
    return p.read_text().strip()


def _load_snapshot(label: str) -> dict[str, str]:
    path = SNAPSHOTS_DIR / f"{label}.json"
    if not path.exists():
        raise SystemExit(
            f"Snapshot '{label}' not found in {SNAPSHOTS_DIR}/\n"
            f"Run:  python3 memory_diff.py snapshot {label}"
        )
    return json.loads(path.read_text())


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "snapshot":
        if len(args) < 2:
            raise SystemExit("Usage: python3 memory_diff.py snapshot <label>")
        cmd_snapshot(args[1])

    elif cmd == "diff":
        if len(args) < 3:
            raise SystemExit("Usage: python3 memory_diff.py diff <before> <after>")
        cmd_diff(args[1], args[2])

    elif cmd == "snapshots":
        cmd_list()

    else:
        raise SystemExit(f"Unknown command '{cmd}'. Use: snapshot, diff, snapshots")


if __name__ == "__main__":
    main()

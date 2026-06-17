"""
Institutional Memory Agent — Streamlit UI

Usage:
    streamlit run app.py
"""

import difflib
import os
from pathlib import Path

import httpx
import streamlit as st
from anthropic import Anthropic

# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Institutional Memory Agent",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* diff rendering */
.diff-add  { background:#0d2b0d; color:#4caf50; padding:1px 12px; font-family:monospace; font-size:0.85em; white-space:pre-wrap; border-radius:2px; }
.diff-del  { background:#2b0d0d; color:#f44336; padding:1px 12px; font-family:monospace; font-size:0.85em; white-space:pre-wrap; border-radius:2px; }
.diff-hunk { background:#0d0d2b; color:#7986cb; padding:1px 12px; font-family:monospace; font-size:0.85em; white-space:pre-wrap; }
.diff-ctx  { color:#666;         padding:1px 12px; font-family:monospace; font-size:0.85em; white-space:pre-wrap; }
/* tool calls */
.tool-memory { color:#ffd54f; font-family:monospace; font-size:0.82em; padding:1px 0; }
.tool-other  { color:#90a4ae; font-family:monospace; font-size:0.82em; padding:1px 0; }
/* unverified badge */
.unverified { background:#b71c1c; color:#fff; border-radius:4px; padding:1px 7px; font-size:0.8em; font-weight:bold; }
</style>
""", unsafe_allow_html=True)

# ── constants ─────────────────────────────────────────────────────────────────

DOC_SETS = {
    "Round 1 — Baseline (onboarding, policy, team)":        Path("synthetic-data/round1"),
    "Round 2 — Policy update (new IAM process, re-org)":    Path("synthetic-data/round2"),
    "Round 3 — Adversarial (suspicious contradictions)":    Path("synthetic-data/round3"),
    "No docs — Introspection only":                         None,
}

DEFAULT_QUERY = (
    "I just joined the company and I need read-only prod access to debug an "
    "issue tomorrow. What do I do? Be specific about the steps and the people "
    "I need to talk to."
)

# ── helpers ───────────────────────────────────────────────────────────────────

@st.cache_resource
def get_client():
    return Anthropic()


def load_ids() -> dict:
    result = {}
    for key, fname in [
        ("agent_id", ".agent_id"),
        ("env_id", ".environment_id"),
        ("store_id", ".memory_store_id"),
    ]:
        p = Path(fname)
        result[key] = p.read_text().strip() if p.exists() else None
    return result


def load_docs(docs_dir: Path | None) -> str:
    if docs_dir is None:
        return ""
    blocks = []
    for p in sorted(docs_dir.glob("*.md")):
        blocks.append(f"=====  DOCUMENT: {p.name}  =====\n{p.read_text()}")
    return "\n\n".join(blocks)


def get_memory_snapshot(client: Anthropic, store_id: str) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
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
    except Exception as e:
        st.warning(f"Could not read memory store: {e}")
    return data


def render_diff_html(before: str, after: str) -> str:
    b_lines = before.splitlines(keepends=True)
    a_lines = after.splitlines(keepends=True)
    diff = list(difflib.unified_diff(b_lines, a_lines, lineterm="", n=2))
    if not diff:
        return "<div class='diff-ctx'>  (no line-level changes)</div>"
    parts = []
    for line in diff[2:]:
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if line.startswith("+"):
            parts.append(f'<div class="diff-add">+ {safe[1:]}</div>')
        elif line.startswith("-"):
            parts.append(f'<div class="diff-del">- {safe[1:]}</div>')
        elif line.startswith("@@"):
            parts.append(f'<div class="diff-hunk">{safe}</div>')
        else:
            parts.append(f'<div class="diff-ctx">  {safe}</div>')
    return "\n".join(parts)


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🧠 Memory Agent")

    ids = load_ids()
    ready = all(ids.values())

    st.subheader("Setup")
    labels = [("Agent", "agent_id"), ("Environment", "env_id"), ("Memory Store", "store_id")]
    for label, key in labels:
        if ids[key]:
            st.success(f"✅ {label}")
        else:
            st.error(f"❌ {label}")

    if not ready:
        st.info("Run `python3 create_agent.py` to set up.")
        st.stop()

    st.divider()

    st.subheader("Document Set")
    doc_label = st.radio("", list(DOC_SETS.keys()), label_visibility="collapsed")
    docs_dir = DOC_SETS[doc_label]

    if docs_dir:
        files = sorted(docs_dir.glob("*.md"))
        with st.expander(f"{len(files)} file(s) in this set"):
            for f in files:
                st.caption(f"📄 {f.name}")
    else:
        st.caption("No documents — agent reads from memory only.")

    st.divider()
    st.caption(f"Agent `…{ids['agent_id'][-8:]}`")
    st.caption(f"Store `…{ids['store_id'][-8:]}`")

    if st.button("🗑 Clear session state", use_container_width=True):
        for k in ["before", "after", "response", "tools"]:
            st.session_state.pop(k, None)
        st.rerun()

# ── main area ─────────────────────────────────────────────────────────────────

st.title("Institutional Memory Agent")
st.caption("Ask a question, pick a document set, and watch the agent reason from memory.")

query = st.text_area(
    "Query",
    value=DEFAULT_QUERY,
    height=90,
    placeholder="Ask anything about company policies, people, or processes…",
)

run_btn = st.button("▶  Run Session", type="primary")

st.divider()

tab_resp, tab_diff, tab_mem = st.tabs(["💬 Response", "📊 Memory Diff", "🗄 Memory State"])

# Placeholders declared inside tabs so they can be updated during streaming
with tab_resp:
    tools_placeholder    = st.empty()
    response_placeholder = st.empty()

with tab_diff:
    diff_placeholder = st.empty()

with tab_mem:
    mem_placeholder = st.empty()


# ── render helpers that write into placeholders ───────────────────────────────

def render_tools(tool_calls: list[str]) -> None:
    if not tool_calls:
        return
    lines = []
    for t in tool_calls:
        css = "tool-memory" if "memory" in t else "tool-other"
        safe = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f'<div class="{css}">⚙ {safe}</div>')
    tools_placeholder.markdown("\n".join(lines), unsafe_allow_html=True)


def render_diff_tab(before: dict, after: dict) -> None:
    all_paths = sorted(set(before) | set(after))
    added     = [p for p in all_paths if p not in before]
    deleted   = [p for p in all_paths if p not in after]
    changed   = [p for p in all_paths if p in before and p in after and before[p] != after[p]]
    unchanged = [p for p in all_paths if p in before and p in after and before[p] == after[p]]

    with diff_placeholder.container():
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Added",     len(added))
        c2.metric("Deleted",   len(deleted))
        c3.metric("Updated",   len(changed))
        c4.metric("Unchanged", len(unchanged))

        if not added and not deleted and not changed:
            st.info("Memory unchanged — agent found no new information to record.")
            return

        st.divider()

        for path in added:
            with st.expander(f"✅ ADDED   {path}", expanded=True):
                st.code(after[path], language="markdown")

        for path in deleted:
            with st.expander(f"🗑 DELETED   {path}", expanded=True):
                st.code(before[path], language="markdown")

        for path in changed:
            with st.expander(f"✏️ UPDATED   {path}", expanded=True):
                st.markdown(render_diff_html(before[path], after[path]), unsafe_allow_html=True)

        for path in unchanged:
            with st.expander(f"➖ UNCHANGED   {path}", expanded=False):
                st.code(after[path], language="markdown")

        # Quarantined claims
        flagged = [(p, c) for p, c in after.items() if "[UNVERIFIED]" in c]
        if flagged:
            st.divider()
            st.markdown("**⚠️ Quarantined claims in memory**")
            for path, content in flagged:
                with st.expander(f"⚠️ {path} contains unverified claims", expanded=True):
                    in_block = False
                    lines = []
                    for line in content.splitlines():
                        if "UNVERIFIED" in line or "Flagged" in line or "flagged" in line:
                            in_block = True
                        if in_block:
                            lines.append(line)
                        if in_block and line.strip() == "":
                            in_block = False
                    st.warning("\n".join(lines))


def render_mem_tab(snapshot: dict) -> None:
    with mem_placeholder.container():
        if not snapshot:
            st.info("Memory store is empty.")
            return

        st.caption(f"{len(snapshot)} file(s)  ·  {sum(len(v) for v in snapshot.values())} total chars")
        st.divider()

        for path, content in sorted(snapshot.items()):
            has_unverified = "[UNVERIFIED]" in content
            badge = " <span class='unverified'>UNVERIFIED CLAIMS</span>" if has_unverified else ""
            label = f"{path}  ({len(content)} chars)"
            with st.expander(label, expanded=True):
                if has_unverified:
                    st.markdown(f"<span class='unverified'>⚠ Contains unverified claims</span>", unsafe_allow_html=True)
                st.markdown(content)


# ── on page load: show current memory state ───────────────────────────────────

if not run_btn:
    if "after" in st.session_state:
        # Restore previous session results
        response_placeholder.markdown(st.session_state.get("response", ""))
        render_tools(st.session_state.get("tools", []))
        render_diff_tab(st.session_state["before"], st.session_state["after"])
        render_mem_tab(st.session_state["after"])
    else:
        # Fresh load — show current memory
        with mem_placeholder.container():
            with st.spinner("Loading current memory state…"):
                client = get_client()
                current = get_memory_snapshot(client, ids["store_id"])
            render_mem_tab(current)

        with diff_placeholder.container():
            st.info("Run a session to see what changes in memory.")

        with response_placeholder.container():
            st.info("Select a document set and hit **▶ Run Session** to start.")

# ── run session ───────────────────────────────────────────────────────────────

if run_btn and query.strip():
    client = get_client()

    # --- snapshot before ---
    with st.spinner("Reading memory store before session…"):
        before = get_memory_snapshot(client, ids["store_id"])

    # --- build message ---
    context = load_docs(docs_dir)
    if context:
        user_msg = (
            "I'm including documents below. Please:\n"
            "1. Check /mnt/memory/ for what you already know.\n"
            "2. Read the documents.\n"
            "3. Reconcile conflicts — use the provenance checklist before updating.\n"
            "4. Answer the question.\n\n"
            f"{context}\n\n{'='*50}\n"
            f"QUESTION: {query}"
        )
    else:
        user_msg = (
            "No new documents today. Read your memory store at /mnt/memory/ and answer:\n\n"
            f"QUESTION: {query}"
        )

    # --- create session ---
    session = client.beta.sessions.create(
        agent=ids["agent_id"],
        environment_id=ids["env_id"],
        title=f"UI — {doc_label[:40]}",
        resources=[{
            "type": "memory_store",
            "memory_store_id": ids["store_id"],
            "access": "read_write",
            "instructions": (
                "Check memory first. Use the provenance checklist before "
                "updating any entry that contradicts existing memory."
            ),
        }],
    )

    # --- stream ---
    full_text = ""
    tool_calls: list[str] = []

    try:
        with client.beta.sessions.events.stream(session.id) as stream:
            client.beta.sessions.events.send(
                session.id,
                events=[{
                    "type": "user.message",
                    "content": [{"type": "text", "text": user_msg}],
                }],
            )
            for event in stream:
                if event.type == "agent.message":
                    for block in event.content:
                        if getattr(block, "type", None) == "text":
                            full_text += block.text
                            response_placeholder.markdown(full_text)

                elif event.type == "agent.tool_use":
                    name = getattr(event, "name", "?")
                    inp  = getattr(event, "input", {}) or {}
                    target = (
                        inp.get("path") or inp.get("file_path")
                        or inp.get("command") or ""
                    )
                    label_str = (
                        f"memory:{name}  {target}"
                        if "/mnt/memory" in str(target)
                        else name
                    )
                    tool_calls.append(label_str)
                    render_tools(tool_calls)

                elif event.type == "session.status_idle":
                    break

    except httpx.ReadError:
        st.warning("Connection reset — response above may be partial. Hit Run again if needed.")

    # --- snapshot after ---
    with st.spinner("Snapshotting memory after session…"):
        after = get_memory_snapshot(client, ids["store_id"])

    # --- persist to session_state ---
    st.session_state["before"]   = before
    st.session_state["after"]    = after
    st.session_state["response"] = full_text
    st.session_state["tools"]    = tool_calls

    # --- render diff + memory tabs ---
    render_diff_tab(before, after)
    render_mem_tab(after)

    st.toast("Session complete — check the Memory Diff tab!", icon="✅")

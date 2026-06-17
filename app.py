"""
Institutional Memory Agent — Streamlit UI

Usage:
    streamlit run app.py
"""

import difflib
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
.diff-add  { background:#0d2b0d; color:#4caf50; padding:1px 10px; font-family:monospace; font-size:0.83em; white-space:pre-wrap; }
.diff-del  { background:#2b0d0d; color:#f44336; padding:1px 10px; font-family:monospace; font-size:0.83em; white-space:pre-wrap; }
.diff-hunk { background:#0d0d2b; color:#7986cb; padding:1px 10px; font-family:monospace; font-size:0.83em; white-space:pre-wrap; }
.diff-ctx  { color:#666;         padding:1px 10px; font-family:monospace; font-size:0.83em; white-space:pre-wrap; }
.tool-mem  { color:#ffd54f; font-family:monospace; font-size:0.8em; }
.tool-std  { color:#90a4ae; font-family:monospace; font-size:0.8em; }
</style>
""", unsafe_allow_html=True)

# ── constants ─────────────────────────────────────────────────────────────────

DOC_SETS = {
    "Round 1 — Baseline (onboarding, policy, team)":      Path("synthetic-data/round1"),
    "Round 2 — Policy update (new IAM process, re-org)":  Path("synthetic-data/round2"),
    "Round 3 — Adversarial (suspicious contradictions)":  Path("synthetic-data/round3"),
    "No docs — Introspection only":                       None,
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
    out = {}
    for key, fname in [("agent_id", ".agent_id"), ("env_id", ".environment_id"), ("store_id", ".memory_store_id")]:
        p = Path(fname)
        out[key] = p.read_text().strip() if p.exists() else None
    return out


def load_docs(docs_dir: Path | None) -> str:
    if not docs_dir:
        return ""
    blocks = []
    for p in sorted(docs_dir.glob("*.md")):
        blocks.append(f"=====  DOCUMENT: {p.name}  =====\n{p.read_text()}")
    return "\n\n".join(blocks)


def get_memory_snapshot(client: Anthropic, store_id: str) -> dict[str, str]:
    data: dict[str, str] = {}
    page = client.beta.memory_stores.memories.list(store_id, path_prefix="/", order_by="path")
    for item in page.data:
        if item.type != "memory":
            continue
        retrieved = client.beta.memory_stores.memories.retrieve(item.id, memory_store_id=store_id)
        data[item.path] = retrieved.content or ""
    return data


def diff_html(before: str, after: str) -> str:
    b = before.splitlines(keepends=True)
    a = after.splitlines(keepends=True)
    diff = list(difflib.unified_diff(b, a, lineterm="", n=2))
    if not diff:
        return "<div class='diff-ctx'>  (no line-level changes)</div>"
    parts = []
    for line in diff[2:]:
        s = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if line.startswith("+"):
            parts.append(f'<div class="diff-add">+ {s[1:]}</div>')
        elif line.startswith("-"):
            parts.append(f'<div class="diff-del">- {s[1:]}</div>')
        elif line.startswith("@@"):
            parts.append(f'<div class="diff-hunk">{s}</div>')
        else:
            parts.append(f'<div class="diff-ctx">  {s}</div>')
    return "\n".join(parts)


def render_diff(before: dict, after: dict) -> None:
    all_paths = sorted(set(before) | set(after))
    added     = [p for p in all_paths if p not in before]
    deleted   = [p for p in all_paths if p not in after]
    changed   = [p for p in all_paths if p in before and p in after and before[p] != after[p]]
    unchanged = [p for p in all_paths if p in before and p in after and before[p] == after[p]]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Added",     len(added))
    c2.metric("Deleted",   len(deleted))
    c3.metric("Updated",   len(changed))
    c4.metric("Unchanged", len(unchanged))

    if not added and not deleted and not changed:
        st.info("Memory unchanged — agent found nothing new to record.")
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
            st.markdown(diff_html(before[path], after[path]), unsafe_allow_html=True)

    for path in unchanged:
        with st.expander(f"➖ UNCHANGED   {path}", expanded=False):
            st.code(after[path], language="markdown")

    flagged = [(p, c) for p, c in after.items() if "[UNVERIFIED]" in c]
    if flagged:
        st.divider()
        st.subheader("⚠️ Quarantined claims")
        for path, _ in flagged:
            st.warning(f"`{path}` contains `[UNVERIFIED]` blocks — the agent held these claims rather than writing them to memory.")


def render_memory(snapshot: dict) -> None:
    if not snapshot:
        st.info("Memory store is empty.")
        return
    st.caption(f"{len(snapshot)} file(s) · {sum(len(v) for v in snapshot.values())} total chars")
    st.divider()
    for path, content in sorted(snapshot.items()):
        flagged = "[UNVERIFIED]" in content
        label = f"{'⚠️ ' if flagged else ''}{path}  ({len(content)} chars)"
        with st.expander(label, expanded=True):
            st.markdown(content)


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🧠 Memory Agent")

    ids = load_ids()
    st.subheader("Setup")
    for label, key in [("Agent", "agent_id"), ("Environment", "env_id"), ("Memory Store", "store_id")]:
        if ids[key]:
            st.success(f"✅ {label}")
        else:
            st.error(f"❌ {label} — run create_agent.py")

    if not all(ids.values()):
        st.info("Run `python3 create_agent.py` in the terminal first.")
        st.stop()

    st.divider()

    st.subheader("Document Set")
    doc_label = st.radio(
        "Select document set",
        list(DOC_SETS.keys()),
        label_visibility="collapsed",
    )
    docs_dir = DOC_SETS[doc_label]

    if docs_dir:
        files = sorted(docs_dir.glob("*.md"))
        with st.expander(f"{len(files)} file(s)"):
            for f in files:
                st.caption(f"📄 {f.name}")
    else:
        st.caption("No docs — agent reads from memory only.")

    st.divider()
    st.caption(f"Agent `…{ids['agent_id'][-8:]}`")
    st.caption(f"Store `…{ids['store_id'][-8:]}`")

    if st.button("🗑 Clear session", use_container_width=True):
        for k in ["before", "after", "response", "tools"]:
            st.session_state.pop(k, None)
        st.rerun()

# ── main layout ───────────────────────────────────────────────────────────────

st.title("Institutional Memory Agent")
st.caption("Type a question, pick a document round, watch the agent reason from memory.")

query = st.text_area("Query", value=DEFAULT_QUERY, height=90)
run_btn = st.button("▶  Run Session", type="primary")
st.divider()

tab_resp, tab_diff, tab_mem = st.tabs(["💬 Response", "📊 Memory Diff", "🗄 Memory State"])

# ── restore previous results on load ─────────────────────────────────────────

if not run_btn:
    with tab_resp:
        if "response" in st.session_state:
            if st.session_state.get("tools"):
                lines = []
                for t in st.session_state["tools"]:
                    css = "tool-mem" if "memory" in t else "tool-std"
                    safe = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    lines.append(f'<div class="{css}">⚙ {safe}</div>')
                st.markdown("\n".join(lines), unsafe_allow_html=True)
                st.divider()
            st.markdown(st.session_state["response"])
        else:
            st.info("Select a document set and hit **▶ Run Session** to start.")

    with tab_diff:
        if "before" in st.session_state:
            render_diff(st.session_state["before"], st.session_state["after"])
        else:
            st.info("Run a session to see what changes in memory.")

    with tab_mem:
        if "after" in st.session_state:
            render_memory(st.session_state["after"])
        else:
            with st.spinner("Loading current memory…"):
                client = get_client()
                snapshot = get_memory_snapshot(client, ids["store_id"])
            render_memory(snapshot)

# ── run session ───────────────────────────────────────────────────────────────

if run_btn and query.strip():
    client = get_client()

    try:
        # 1. snapshot before
        with st.spinner("Reading memory before session…"):
            before = get_memory_snapshot(client, ids["store_id"])

        # 2. build user message
        context = load_docs(docs_dir)
        if context:
            user_msg = (
                "I'm including documents below. Please:\n"
                "1. Check /mnt/memory/ for what you already know.\n"
                "2. Read the documents.\n"
                "3. Reconcile conflicts — use the provenance checklist before updating.\n"
                "4. Answer the question.\n\n"
                f"{context}\n\n{'='*50}\nQUESTION: {query}"
            )
        else:
            user_msg = (
                "No new documents. Read your memory store at /mnt/memory/ and answer:\n\n"
                f"QUESTION: {query}"
            )

        # 3. create session
        with st.spinner("Creating session…"):
            session = client.beta.sessions.create(
                agent=ids["agent_id"],
                environment_id=ids["env_id"],
                title=f"UI — {doc_label[:40]}",
                resources=[{
                    "type": "memory_store",
                    "memory_store_id": ids["store_id"],
                    "access": "read_write",
                    "instructions": (
                        "Check memory first. Use the provenance checklist "
                        "before updating any entry that contradicts existing memory."
                    ),
                }],
            )

        # 4. stream response — all inside tab_resp context
        full_text = ""
        tool_calls: list[str] = []

        with tab_resp:
            st.caption(f"📂 {doc_label}")
            tool_box  = st.empty()
            resp_box  = st.empty()
            warn_box  = st.empty()

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
                                    resp_box.markdown(full_text)

                        elif event.type == "agent.tool_use":
                            name   = getattr(event, "name", "?")
                            inp    = getattr(event, "input", {}) or {}
                            target = inp.get("path") or inp.get("file_path") or inp.get("command") or ""
                            tag    = f"memory:{name}  {target}" if "/mnt/memory" in str(target) else name
                            tool_calls.append(tag)
                            css_lines = []
                            for t in tool_calls:
                                css = "tool-mem" if "memory" in t else "tool-std"
                                safe = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                                css_lines.append(f'<div class="{css}">⚙ {safe}</div>')
                            tool_box.markdown("\n".join(css_lines), unsafe_allow_html=True)

                        elif event.type == "session.status_idle":
                            break

            except httpx.ReadError:
                warn_box.warning("Connection reset — response above may be partial. Hit Run again if needed.")

            if not full_text:
                resp_box.warning("No response received from the agent. Check your API key and try again.")

        # 5. snapshot after
        with st.spinner("Snapshotting memory after session…"):
            after = get_memory_snapshot(client, ids["store_id"])

        # 6. persist
        st.session_state.update({
            "before":   before,
            "after":    after,
            "response": full_text,
            "tools":    tool_calls,
        })

        # 7. render diff + memory tabs
        with tab_diff:
            render_diff(before, after)

        with tab_mem:
            render_memory(after)

        if full_text:
            st.toast("Session complete — check the Memory Diff tab!", icon="✅")

    except Exception as e:
        st.error(f"Session failed: {e}")
        st.exception(e)

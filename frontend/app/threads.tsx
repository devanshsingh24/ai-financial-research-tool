"use client";

// Research threads panel: the persistence layer made visible. Lists the user's saved
// threads, shows the open thread's conversation, recalled memories and status, and its
// checkpoint history with replay (re-run from a step) and fork (edit the question, re-run).

import { useEffect, useState } from "react";
import { api, AskResponse, Checkpoint, ThreadOut, ThreadState, ThreadStatus } from "./api";

const STEP_LABELS: Record<string, string> = {
  __start__: "Question received", start_turn: "Start turn", load_memories: "Recall memories",
  retrieve_scored: "Retrieve sources", draft: "Draft answer", plan_search: "Plan web search",
  ask_permission: "Search gate", web_search: "Web search", final_answer: "Write web report",
  review_report: "Report gate", unverified: "Mark unverified", finish: "Finish turn", save_memory: "Save memories",
};
const GATES = new Set(["ask_permission", "review_report"]);
const STATUS_TEXT: Record<ThreadStatus, string> = { idle: "NEW", running: "RUNNING", interrupted: "AWAITING YOU", completed: "DONE", failed: "FAILED" };

function ago(iso: string) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

// "next" is what runs after a checkpoint, so a checkpoint is the moment *before* that step.
function stepLabel(cp: Checkpoint) {
  if (!cp.next.length) return "Turn complete";
  const node = cp.next[0];
  return GATES.has(node) ? `Paused at ${STEP_LABELS[node].toLowerCase()}` : `Before: ${STEP_LABELS[node] ?? node}`;
}

type Props = {
  userId: string;
  onUserChange: (userId: string) => void;
  activeId: string | null;
  version: number;                 // bumped by the terminal after every run
  backend: string | null;          // "sqlite" | "postgres" from /health
  onOpen: (threadId: string) => void;
  onNew: () => void;
  onChanged: (threadId: string) => void;  // a replay/fork/retry changed the open thread: reload it
  onDeleted: () => void;
};

export function ThreadsPanel({ userId, onUserChange, activeId, version, backend, onOpen, onNew, onChanged, onDeleted }: Props) {
  const [threads, setThreads] = useState<ThreadOut[]>([]);
  const [detail, setDetail] = useState<ThreadState | null>(null);
  const [history, setHistory] = useState<Checkpoint[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);  // what is running, for button labels
  const [message, setMessage] = useState("");
  const [fork, setFork] = useState<{ checkpointId: string; question: string } | null>(null);
  const [userDraft, setUserDraft] = useState(userId);
  useEffect(() => setUserDraft(userId), [userId]);

  useEffect(() => {
    api<ThreadOut[]>("/threads", userId).then(setThreads).catch((e) => setMessage(e.message));
  }, [userId, version]);

  useEffect(() => {
    setFork(null);
    if (!activeId) { setDetail(null); setHistory([]); return; }
    api<ThreadState>(`/threads/${activeId}`, userId).then(setDetail).catch(() => setDetail(null));
    api<Checkpoint[]>(`/threads/${activeId}/history?limit=100`, userId).then(setHistory).catch(() => setHistory([]));
  }, [activeId, userId, version]);

  async function act(label: string, fn: () => Promise<AskResponse | void>, done: string) {
    if (!activeId) return;
    setBusy(label); setMessage("");
    try { const result = await fn(); setMessage(result?.status === "needs_confirmation" ? `${done} It is paused at a gate: answer it in the terminal above.` : done); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Request failed."); }
    finally { setBusy(null); onChanged(activeId); }
  }
  const replay = (cp: Checkpoint) => act("replay", () => api<AskResponse>(`/threads/${activeId}/replay`, userId, { method: "POST", body: { checkpoint_id: cp.checkpoint_id } }), "Replayed from that checkpoint as a new branch. The earlier branch is kept in the history.");
  const runFork = () => fork && act("fork", async () => (await api<{ result: AskResponse | null }>(`/threads/${activeId}/fork`, userId, { method: "POST", body: { checkpoint_id: fork.checkpointId, values: { question: fork.question } } })).result ?? undefined, "Forked with the edited question. The original branch is kept in the history.");
  const retry = () => act("retry", () => api<AskResponse>(`/threads/${activeId}/retry`, userId, { method: "POST" }), "Resumed from the last checkpoint; finished steps were not re-run.");
  async function remove() {
    if (!activeId || !window.confirm("Delete this thread and all its checkpoints?")) return;
    setBusy("delete");
    try { await api(`/threads/${activeId}`, userId, { method: "DELETE" }); setMessage(""); onDeleted(); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Delete failed."); } finally { setBusy(null); }
  }
  const commitUser = () => { const v = userDraft.trim(); if (/^[A-Za-z0-9_.-]{1,64}$/.test(v) && v !== userId) onUserChange(v); else setUserDraft(userId); };
  const running = detail?.thread.status === "running";

  return <section className="threads" id="threads">
    <div className="threads-head">
      <div><p className="eyebrow">03 / PERSISTENCE</p><h2>Research threads</h2>
        <p className="threads-lede">Every web-fallback question is saved as a thread. Threads survive restarts, can be resumed at a gate, retried after a failure, or replayed and forked from any checkpoint.</p></div>
      <div className="threads-meta">
        <span className="badge">{backend ? `${backend.toUpperCase()} · CHECKPOINTED` : "PERSISTENCE OFFLINE"}</span>
        <label className="user-field" title="No sign-in yet: this ID scopes threads and memories. Other IDs can't see them."><span>USER</span><input value={userDraft} onChange={(e) => setUserDraft(e.target.value)} onBlur={commitUser} onKeyDown={(e) => e.key === "Enter" && commitUser()} maxLength={64} aria-label="User ID" /></label>
      </div>
    </div>
    <div className="threads-body">
      <aside className="thread-list">
        <button className="new-thread" onClick={onNew}>+ New thread</button>
        {threads.length === 0 && <p className="empty">No threads yet for “{userId}”. Ask a question with web fallback on.</p>}
        {threads.map((t) => <button key={t.thread_id} className={`thread-item${t.thread_id === activeId ? " active" : ""}`} onClick={() => onOpen(t.thread_id)}>
          <b>{t.title || "Untitled thread"}</b><span><i className={`status ${t.status}`}>{STATUS_TEXT[t.status]}</i>{ago(t.updated_at)}</span></button>)}
      </aside>
      <div className="thread-detail">
        {!detail ? <p className="empty">Open a thread to see its conversation, memories and checkpoint history. The research terminal above continues the open thread.</p> : <>
          <div className="detail-head"><div><p className="answer-label">OPEN IN TERMINAL · <a href="#terminal">GO TO TERMINAL ↑</a></p><h3>{detail.thread.title || "Untitled thread"}</h3></div>
            <div className="confirm-actions">
              {detail.thread.status === "failed" && <button className="search-btn" onClick={retry} disabled={!!busy}>{busy === "retry" ? "Resuming…" : "Retry from checkpoint"}<span>↻</span></button>}
              <button className="draft-btn" onClick={remove} disabled={!!busy || running}>Delete</button></div></div>
          {detail.thread.status === "failed" && <p className="thread-error">Last run failed: {detail.thread.error ?? "unknown error"}</p>}
          {detail.thread.status === "interrupted" && <p className="search-meta">Paused at the {detail.pending?.kind === "report" ? "report" : "search"} gate. Answer it in the terminal above; it survives restarts.</p>}
          {message && <p className="search-meta">{message}</p>}

          <p className="answer-label">CONVERSATION · {detail.messages.length} MESSAGES</p>
          {detail.summary && <p className="summary"><b>Earlier turns (summarized):</b> {detail.summary}</p>}
          <ol className="messages">{detail.messages.map((m, i) => <li key={i} className={m.role}><span>{m.role === "user" ? "YOU" : "FINSIGHT"}</span><p>{m.content}</p></li>)}</ol>
          {detail.messages.length === 0 && <p className="empty">No questions yet. Ask one in the terminal above.</p>}

          <p className="answer-label">LONG-TERM MEMORY RECALLED AT THREAD START</p>
          {detail.memories.length ? <ul className="memories">{detail.memories.map((m) => <li key={m}>{m}</li>)}</ul> : <p className="empty">Nothing recalled yet. Preferences, tracked companies and report summaries are saved after each answer and recalled in your next threads.</p>}

          <button className="history-toggle" onClick={() => setShowHistory((v) => !v)}>{showHistory ? "▾" : "▸"} CHECKPOINT HISTORY · {history.length}</button>
          {showHistory && <ol className="history">{history.map((cp) => {
            const question = typeof cp.values.question === "string" ? cp.values.question : "";
            const canRun = cp.next.length > 0 && !GATES.has(cp.next[0]);
            // Fork only before retrieval, so the edited question gets fresh sources too.
            const canFork = cp.next[0] === "retrieve_scored" && !!question;
            return <li key={cp.checkpoint_id} className={cp.checkpoint_id === detail.checkpoint_id ? "head" : ""}>
              <div className="cp-row"><span className="cp-step">#{cp.step ?? "–"}</span><span className="cp-label">{stepLabel(cp)}{cp.checkpoint_id === detail.checkpoint_id && <i className="status completed">HEAD</i>}</span>
                {canRun && <span className="cp-actions"><button onClick={() => replay(cp)} disabled={!!busy || running}>{busy === "replay" ? "…" : "Replay"}</button>{canFork && <button onClick={() => setFork({ checkpointId: cp.checkpoint_id, question })} disabled={!!busy || running} title="Edit the question at this point and run a new branch">Fork</button>}</span>}</div>
              {question && <small>“{question}”</small>}
              {fork?.checkpointId === cp.checkpoint_id && <div className="fork-box"><input value={fork.question} onChange={(e) => setFork({ ...fork, question: e.target.value })} maxLength={1500} aria-label="Edited question" /><button className="search-btn" onClick={runFork} disabled={!!busy || fork.question.trim().length < 3}>{busy === "fork" ? "Running…" : "Run fork"}<span>→</span></button><button className="draft-btn" onClick={() => setFork(null)}>Cancel</button></div>}
            </li>;
          })}</ol>}
        </>}
      </div>
    </div>
  </section>;
}

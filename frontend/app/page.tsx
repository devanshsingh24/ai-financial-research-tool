"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, API_URL, AskResponse, GateAction, GateKind, stripSourcesFooter, streamEvents, ThreadOut, ThreadState, WebSearchInfo } from "./api";
import { ThreadsPanel } from "./threads";

// kind drives the tag: answered from sources, a draft awaiting the search decision, a web answer,
// a web report awaiting review, or an unverified draft.
type ResultKind = "sources" | "draft" | "web" | "review" | "unverified";
type ResearchResult = { answer: string; sources: string[]; web?: WebSearchInfo | null; kind: ResultKind };
// The thread is paused at a gate: the search gate (planned query, editable) or the report gate (report, editable).
type PendingGate = { threadId: string; kind: GateKind; query: string };
type ActiveThread = { id: string; title: string };

const USER_KEY = "finsight-user";
const hostname = (url: string) => { try { return new URL(url).hostname.replace("www.", ""); } catch { return url; } };
const appendToken = (token: string) => (prev: ResearchResult | null): ResearchResult => ({ answer: (prev?.answer ?? "") + token, sources: prev?.sources ?? [], web: prev?.web ?? null, kind: prev?.kind ?? "sources" });
const isUnverified = (answer: string) => answer.includes("⚠");

function resultTag(result: ResearchResult) {
  if (result.kind === "web") return result.web?.used ? <span className="tag web">{result.web.from_cache ? "WEB · CACHED · 0 CREDITS" : "WEB · 1 CREDIT"}</span> : <span className="tag web">WEB · SEARCHING</span>;
  if (result.kind === "review") return <span className="tag web">WEB · AWAITING YOUR REVIEW</span>;
  if (result.kind === "draft") return <span className="tag warn">DRAFT · NOT VERIFIED</span>;
  if (result.kind === "unverified") return <span className="tag warn">UNVERIFIED</span>;
  return <span className="tag">YOUR SOURCES</span>;
}
// A completed answer as the terminal shows it.
function answeredResult(d: AskResponse): ResearchResult {
  const web = d.web_search ?? null; const answer = stripSourcesFooter(d.answer ?? "");
  const kind: ResultKind = web?.used && !web.error && !isUnverified(answer) ? "web" : isUnverified(answer) ? "unverified" : "sources";
  return { answer, sources: d.sources ?? [], web, kind };
}
const dropIfEmpty = (prev: ResearchResult | null) => (prev && prev.answer ? prev : null);

export default function Dashboard() {
  const [urls, setUrls] = useState(["", "", ""]);
  const [question, setQuestion] = useState("");
  const [isIndexing, setIsIndexing] = useState(false);
  const [isResearching, setIsResearching] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [notice, setNotice] = useState("Bring your sources. We’ll ground every answer in them.");
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [webFallback, setWebFallback] = useState(true);
  const [pending, setPending] = useState<PendingGate | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);
  const [editedQuery, setEditedQuery] = useState("");
  const [editedReport, setEditedReport] = useState("");
  const [isEditingReport, setIsEditingReport] = useState(false);
  // Persistence: who we are (X-User-Id), which thread the terminal continues, and a counter that
  // tells the threads panel to reload after a run.
  const [userId, setUserId] = useState("local");
  const [activeThread, setActiveThread] = useState<ActiveThread | null>(null);
  const [threadsVersion, setThreadsVersion] = useState(0);
  const [backend, setBackend] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);  // "ollama:llama3" or "groq:openai/gpt-oss-120b"
  const refreshThreads = () => setThreadsVersion((v) => v + 1);

  useEffect(() => { const saved = window.localStorage.getItem(USER_KEY); if (saved) setUserId(saved); }, []);
  useEffect(() => { fetch(`${API_URL}/health`).then((r) => r.json()).then((d) => { setBackend(d.persistence ?? null); setModel(d.llm ?? null); }).catch(() => setBackend(null)); }, []);

  const updateUrl = (index: number, value: string) => setUrls((current) => current.map((url, i) => i === index ? value : url));
  async function ingestSources(event: FormEvent) {
    event.preventDefault(); const sources = urls.map((url) => url.trim()).filter(Boolean);
    if (!sources.length) return setNotice("Add at least one public article URL to create a collection.");
    setIsIndexing(true); setResult(null); setNotice("Reading articles and building your private research index…");
    try { const response = await fetch(`${API_URL}/api/process`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls: sources }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail ?? "Indexing failed."); setNotice(`${data.indexed_documents} research passages indexed from ${data.sources.length} source${data.sources.length === 1 ? "" : "s"}.`); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsIndexing(false); }
  }

  // Show a gate the thread is paused at (from a stream event or a loaded thread).
  function showGate(d: AskResponse, fallbackSources: string[] = []) {
    if (d.kind === "report") {
      const report = stripSourcesFooter(d.answer ?? "");
      setResult({ answer: report, sources: d.sources?.length ? d.sources : fallbackSources, web: null, kind: "review" });
      setPending({ threadId: d.thread_id, kind: "report", query: "" }); setEditedReport(report);
      setNotice("Review the web report: approve it, edit it, or reject it for the unverified draft.");
    } else {
      setResult((prev) => ({ answer: d.draft ?? prev?.answer ?? "", sources: prev?.sources?.length ? prev.sources : fallbackSources, web: null, kind: "draft" }));
      const query = d.search?.query ?? ""; setPending({ threadId: d.thread_id, kind: "web_search", query }); setEditedQuery(query);
      setNotice("Your sources may not fully answer this. Search the web?");
    }
  }

  // Load a saved thread into the terminal; the next question continues it.
  const openThread = useCallback(async (threadId: string) => {
    setPending(null); setIsEditingReport(false);
    try {
      const d = await api<ThreadState>(`/threads/${threadId}`, userId);
      const title = d.thread.title || "Untitled thread";
      setActiveThread({ id: threadId, title });
      if (d.pending) { setResult(null); showGate(d.pending); setNotice(`Thread “${title}” is paused at the ${d.pending.kind === "report" ? "report" : "search"} gate.`); }
      else if (d.last) { setResult(answeredResult(d.last)); setNotice(d.thread.status === "failed" ? "This thread's last run failed. Retry it from the threads panel below." : `Continuing “${title}”. Ask a follow-up; earlier turns are remembered.`); }
      else { setResult(null); setNotice(d.thread.status === "failed" ? "This thread's last run failed. Retry it from the threads panel below." : `Continuing “${title}”. Ask its first question.`); }
    } catch (error) { setActiveThread(null); setResult(null); setNotice(error instanceof Error ? error.message : "Could not load the thread."); }
  }, [userId]);

  function newThread() { setActiveThread(null); setPending(null); setIsEditingReport(false); setResult(null); setNotice("New thread: your next question starts it."); }
  function changeUser(next: string) { window.localStorage.setItem(USER_KEY, next); setUserId(next); newThread(); setNotice(`Now working as “${next}”. Threads and memories are separate per user.`); }

  async function askResearch(event: FormEvent) {
    event.preventDefault(); if (!question.trim()) return;
    const wasPaused = !!pending;
    setPending(null); setIsEditingReport(false);
    return webFallback ? askWithWebFallback(wasPaused) : streamAnswer();
  }
  // Web fallback ON: ask on the open thread (or a new one). Stream an answer from your sources;
  // if they can't answer, the streamed text becomes a draft and we ask before spending a Tavily credit.
  async function askWithWebFallback(wasPaused: boolean) {
    setIsResearching(true); setIsStreaming(true); setNotice("Checking your sources…");
    setResult({ answer: "", sources: [], web: null, kind: "sources" });
    try {
      // A thread paused at a gate can't take a new question, so that one starts a new thread.
      let thread = activeThread && !wasPaused ? activeThread : null;
      if (!thread) {
        const created = await api<ThreadOut>("/threads", userId, { method: "POST", body: { title: question.trim().slice(0, 200) } });
        thread = { id: created.thread_id, title: created.title }; setActiveThread(thread); refreshThreads();
      }
      await streamEvents(`/threads/${thread.id}/messages/stream`, { question }, userId, (event, data) => {
        if (event === "sources") { const sources = data as string[]; setResult((prev) => ({ ...(prev ?? { answer: "", kind: "sources" }), sources })); setNotice("Writing answer…"); }
        else if (event === "token") setResult(appendToken(data as string));
        else if (event === "needs_confirmation") showGate(data as AskResponse);
        else if (event === "answered") {
          setResult(answeredResult(data as AskResponse));
          setNotice("Answered from your sources. No web search needed (0 credits). Saved to the thread.");
        }
      });
    }
    catch (error) { setResult(dropIfEmpty); setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsResearching(false); setIsStreaming(false); refreshThreads(); }
  }
  // Answer the gate the thread is paused at. Search gate: approve (or edit = search the edited
  // query) / reject. Report gate: approve / edit (send the edited report) / reject (unverified draft).
  async function resumeGate(action: GateAction) {
    if (!pending) return;
    const { threadId, kind } = pending;
    const queryEdited = kind === "web_search" && action === "approve" && editedQuery.trim() !== "" && editedQuery.trim() !== pending.query;
    const finalAction: GateAction = queryEdited ? "edit" : action;
    const body = finalAction === "edit" ? (kind === "web_search" ? { action: "edit", query: editedQuery.trim() } : { action: "edit", draft: editedReport }) : { action: finalAction };
    setPending(null); setIsEditingReport(false); setIsConfirming(true);
    const searching = kind === "web_search" && action !== "reject";
    if (searching) { setIsStreaming(true); setResult((prev) => ({ answer: "", sources: prev?.sources ?? [], web: null, kind: "web" })); setNotice("Starting the web search…"); }
    else if (kind === "web_search") setNotice("Using the draft from your sources…");
    else setNotice(action === "reject" ? "Discarding the web report…" : "Saving the report…");
    let gotToken = false;
    try {
      await streamEvents(`/threads/${threadId}/resume/stream`, body, userId, (event, data) => {
        if (event === "status") setNotice(data as string);
        else if (event === "token") { if (!gotToken) { gotToken = true; setNotice("Writing the verified answer…"); } setResult(appendToken(data as string)); }
        else if (event === "needs_confirmation") showGate(data as AskResponse);
        else if (event === "answered") {
          const d = data as AskResponse; const shown = answeredResult(d);
          setResult(action === "reject" ? { ...shown, kind: "unverified" } : shown);
          if (kind === "web_search" && action === "reject") setNotice("Showing the unverified draft. No web search was made (0 credits).");
          else if (kind === "report" && action === "reject") setNotice("Report rejected. Showing the unverified draft from your sources.");
          else if (shown.kind !== "web") setNotice("Web search failed. Showing the unverified draft.");
          else if (kind === "report" && finalAction === "edit") setNotice("Your edited report was saved to the thread.");
          else if (d.web_search?.from_cache) setNotice("Answered with cached web results (0 credits).");
          else setNotice("Answered with 1 web search (1 credit). Review the cited sources below.");
        }
      });
    }
    catch (error) { setResult(dropIfEmpty); setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsConfirming(false); setIsStreaming(false); refreshThreads(); }
  }
  // Web fallback OFF: live token streaming from your sources only (not saved to a thread).
  async function streamAnswer() {
    setIsResearching(true); setIsStreaming(true); setNotice("Retrieving evidence…");
    setResult({ answer: "", sources: [], web: null, kind: "sources" });
    try {
      await streamEvents("/api/query/stream", { question }, userId, (event, data) => {
        if (event === "sources") { const sources = data as string[]; setResult((prev) => ({ ...(prev ?? { answer: "", kind: "sources" }), sources })); setNotice("Writing answer…"); }
        else if (event === "token") setResult(appendToken(data as string));
        else if (event === "done") setNotice("Analysis ready. Review the cited source set below. (Sources-only answers aren't saved to threads.)");
      });
    }
    catch (error) { setResult(dropIfEmpty); setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsResearching(false); setIsStreaming(false); }
  }
  // The threads panel replayed, forked, retried or deleted a thread: reload it into the terminal.
  function threadChanged(threadId: string) {
    refreshThreads();
    void openThread(threadId);
  }

  const busy = isResearching || isConfirming;
  return <main>
    <nav><a className="brand" href="#top"><span>✦</span> FinSight</a><div className="nav-status"><i /> {model && !model.startsWith("ollama:") ? model.replace(":", " · ").toUpperCase() : "LOCAL INTELLIGENCE"}{backend ? ` · ${backend.toUpperCase()} CHECKPOINTS` : ""}</div><a className="ghost" href="#threads">Research threads</a></nav>
    <section className="hero" id="top"><p className="eyebrow">AI-POWERED FINANCIAL RESEARCH</p><h1>Clarity for every<br /><em>market-moving</em> question.</h1><p className="lede">Turn your selected financial reporting into defensible, source-backed analysis — entirely on your machine.</p><div className="metrics"><div><b>Private</b><span>local models</span></div><div><b>Grounded</b><span>source evidence</span></div><div><b>Persistent</b><span>resumable threads</span></div></div></section>
    <section className="workspace"><div className="source-panel"><div className="panel-heading"><div><p className="eyebrow">01 / BUILD CONTEXT</p><h2>Research collection</h2></div><span className="badge">LOCAL</span></div><form onSubmit={ingestSources}>{urls.map((url, index) => <label className="url-field" key={index}><span>0{index + 1}</span><input type="url" value={url} onChange={(e) => updateUrl(index, e.target.value)} placeholder="Paste a news or market analysis URL" /></label>)}<button className="primary" disabled={isIndexing}>{isIndexing ? "Indexing sources…" : "Create research collection"}<span>→</span></button></form></div>
      <div className="analysis-panel" id="terminal"><div className="terminal-heading"><div><p className="eyebrow">02 / ASK FINSIGHT</p><h2>Research terminal</h2></div><label className="web-toggle" title="When your sources can't answer, offer one web search (1 Tavily credit). Off = live-streamed answers from your sources only."><input type="checkbox" checked={webFallback} onChange={(e) => setWebFallback(e.target.checked)} disabled={busy} /><span className="track" aria-hidden="true" /><span className="toggle-text">WEB FALLBACK<small>{webFallback ? "asks before searching" : "sources only · live stream"}</small></span></label></div>
        <div className="thread-chip">{webFallback
          ? activeThread ? <><span>THREAD</span><b title={activeThread.title}>{activeThread.title || "Untitled thread"}</b><button onClick={newThread} disabled={busy}>+ New</button></> : <><span>THREAD</span><b>New thread</b><small>your next question starts one</small></>
          : <><span>THREAD</span><small>sources-only answers aren’t saved</small></>}</div>
        <form onSubmit={askResearch} className="ask"><textarea value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={activeThread && webFallback ? "Ask a follow-up…" : "What is the key risk to the company’s outlook?"} /><button aria-label="Run research" disabled={busy}>{isResearching ? "…" : "↑"}</button></form><div className="notice"><span className="pulse" />{notice}</div>
        {result && <article className="answer" aria-live="polite"><p className="answer-label">SYNTHESIS {resultTag(result)}</p><p className={`answer-text${result.kind === "draft" ? " draft" : ""}`}>{result.answer}{isStreaming && <span className="cursor" aria-hidden="true" />}</p>
          {pending?.kind === "web_search" && !isStreaming && <div className="confirm-row"><p>Search the web for a verified answer?<small>Runs one search · uses 1 Tavily credit (0 if cached)</small></p>
            <label className="gate-field"><span>QUERY</span><input value={editedQuery} onChange={(e) => setEditedQuery(e.target.value)} maxLength={400} disabled={isConfirming} aria-label="Planned web search query" /></label>
            <div className="confirm-actions"><button className="search-btn" onClick={() => resumeGate("approve")} disabled={isConfirming || !editedQuery.trim()}>Search the web<span>↗</span></button><button className="draft-btn" onClick={() => resumeGate("reject")} disabled={isConfirming}>No, use draft</button></div></div>}
          {pending?.kind === "report" && !isStreaming && <div className="confirm-row"><p>Use this web report?<small>Approve it as is, edit it, or reject it for the unverified draft (no extra credits)</small></p>
            {isEditingReport && <textarea className="gate-report" value={editedReport} onChange={(e) => setEditedReport(e.target.value)} disabled={isConfirming} aria-label="Edit the report" />}
            <div className="confirm-actions">{isEditingReport
              ? <><button className="search-btn" onClick={() => resumeGate("edit")} disabled={isConfirming || !editedReport.trim()}>Save edited report<span>✓</span></button><button className="draft-btn" onClick={() => setIsEditingReport(false)} disabled={isConfirming}>Cancel edit</button></>
              : <><button className="search-btn" onClick={() => resumeGate("approve")} disabled={isConfirming}>Approve report<span>✓</span></button><button className="draft-btn" onClick={() => setIsEditingReport(true)} disabled={isConfirming}>Edit</button><button className="draft-btn" onClick={() => resumeGate("reject")} disabled={isConfirming}>Reject</button></>}</div></div>}
          {result.kind === "web" && result.web?.used && <p className="search-meta">Searched: “{result.web.query}” · {result.web.topic}{result.web.time_range ? ` · past ${result.web.time_range}` : ""}</p>}
          {result.sources.length > 0 && <><p className="answer-label">EVIDENCE</p><ul>{result.sources.map((source, i) => <li key={source}><a href={source} target="_blank" rel="noreferrer">{result.kind === "web" && result.web?.used ? `[${i + 1}] ` : ""}{hostname(source)} ↗</a></li>)}</ul></>}</article>}</div></section>
    <ThreadsPanel userId={userId} onUserChange={changeUser} activeId={activeThread?.id ?? null} version={threadsVersion} backend={backend}
      onOpen={(id) => void openThread(id)} onNew={newThread} onChanged={threadChanged}
      onDeleted={() => { newThread(); refreshThreads(); setNotice("Thread deleted with all its checkpoints."); }} />
    <section className="future"><div><p className="eyebrow">ON THE HORIZON</p><h2>From research assistant<br />to <em>market intelligence desk.</em></h2></div><div className="feature-grid"><article><span>◌</span><h3>Live web discovery</h3><p>Agent-led searching across trusted financial sources, launched from a single research brief.</p><small>COMING SOON</small></article><article><span>⌁</span><h3>Multi-tool agents</h3><p>Specialist agents that compare filings, news, earnings calls, and market signals before they respond.</p><small>PLANNED</small></article><article><span>⌘</span><h3>Continuous watchlists</h3><p>Monitor companies and themes, then surface only developments that materially change the thesis.</p><small>PLANNED</small></article></div></section>
    <footer><span>FINSIGHT / LOCAL RESEARCH TERMINAL</span><span>BUILT FOR DELIBERATE DECISIONS</span></footer>
  </main>;
}

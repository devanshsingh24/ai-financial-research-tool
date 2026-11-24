"use client";

import { FormEvent, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type WebSearchInfo = { used: boolean; from_cache: boolean; query: string; topic: string; time_range: string | null; error: string | null };
// kind drives the tag: answered from sources, a draft awaiting the search decision, a web answer, or an unverified draft.
type ResultKind = "sources" | "draft" | "web" | "unverified";
type ResearchResult = { answer: string; sources: string[]; web?: WebSearchInfo | null; kind: ResultKind };
type StreamEvent = { event: string; data: unknown };
type AskResponse = { status: "answered" | "needs_confirmation"; thread_id: string; prompt?: string | null; draft?: string | null; answer?: string | null; sources?: string[]; web_search?: WebSearchInfo | null };
type PendingSearch = { threadId: string };

const hostname = (url: string) => { try { return new URL(url).hostname.replace("www.", ""); } catch { return url; } };
// The backend appends "Sources:\n[1] url…" to web answers; the UI lists sources itself.
const stripSourcesFooter = (answer: string) => answer.replace(/\n\nSources:\n[\s\S]*$/, "");
const appendToken = (token: string) => (prev: ResearchResult | null): ResearchResult => ({ answer: (prev?.answer ?? "") + token, sources: prev?.sources ?? [], web: prev?.web ?? null, kind: prev?.kind ?? "sources" });

function resultTag(result: ResearchResult) {
  if (result.kind === "web") return result.web?.used ? <span className="tag web">{result.web.from_cache ? "WEB · CACHED · 0 CREDITS" : "WEB · 1 CREDIT"}</span> : <span className="tag web">WEB · SEARCHING</span>;
  if (result.kind === "draft") return <span className="tag warn">DRAFT · NOT VERIFIED</span>;
  if (result.kind === "unverified") return <span className="tag warn">UNVERIFIED</span>;
  return <span className="tag">YOUR SOURCES</span>;
}
const dropIfEmpty =(prev: ResearchResult | null) => (prev && prev.answer ? prev : null);

// POSTs to a Server-Sent-Events endpoint and calls onEvent for each event until the stream ends.
async function streamEvents(path: string, body: unknown, onEvent: (event: string, data: unknown) => void): Promise<void> {
  const response = await fetch(`${API_URL}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok || !response.body) { const data = await response.json().catch(() => ({})); throw new Error(data.detail ?? "Research failed."); }
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
  while (true) {
    const { done, value } = await reader.read(); if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n"); buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const parsed = parseEvent(block); if (!parsed) continue;
      if (parsed.event === "error") throw new Error((parsed.data as { detail?: string }).detail ?? "Research failed.");
      onEvent(parsed.event, parsed.data);
    }
  }
}

// Parses one Server-Sent Event block ("event: x\ndata: <json>").
function parseEvent(block: string): StreamEvent | null {
  let event = "message"; let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch { return null; }
}

export default function Dashboard() {
  const [urls, setUrls] = useState(["", "", ""]);
  const [question, setQuestion] = useState("");
  const [isIndexing, setIsIndexing] = useState(false);
  const [isResearching, setIsResearching] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [notice, setNotice] = useState("Bring your sources. We’ll ground every answer in them.");
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [webFallback, setWebFallback] = useState(true);
  const [pending, setPending] = useState<PendingSearch | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);
  const updateUrl = (index: number, value: string) => setUrls((current) => current.map((url, i) => i === index ? value : url));
  async function ingestSources(event: FormEvent) {
    event.preventDefault(); const sources = urls.map((url) => url.trim()).filter(Boolean);
    if (!sources.length) return setNotice("Add at least one public article URL to create a collection.");
    setIsIndexing(true); setResult(null); setNotice("Reading articles and building your private research index…");
    try { const response = await fetch(`${API_URL}/api/process`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls: sources }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail ?? "Indexing failed."); setNotice(`${data.indexed_documents} research passages indexed from ${data.sources.length} source${data.sources.length === 1 ? "" : "s"}.`); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsIndexing(false); }
  }
  async function askResearch(event: FormEvent) {
    event.preventDefault(); if (!question.trim()) return;
    setPending(null);
    return webFallback ? askWithWebFallback() : streamAnswer();
  }
  // Web fallback ON: stream an answer from your sources; if they can't answer, the streamed
  // text becomes a draft and we ask before spending a Tavily credit.
  async function askWithWebFallback() {
    setIsResearching(true); setIsStreaming(true); setNotice("Checking your sources…");
    setResult({ answer: "", sources: [], web: null, kind: "sources" });
    try {
      await streamEvents("/ask/stream", { question }, (event, data) => {
        if (event === "sources") { const sources = data as string[]; setResult((prev) => ({ ...(prev ?? { answer: "", kind: "sources" }), sources })); setNotice("Writing answer…"); }
        else if (event === "token") setResult(appendToken(data as string));
        else if (event === "needs_confirmation") {
          const d = data as AskResponse;
          setResult((prev) => ({ answer: d.draft ?? prev?.answer ?? "", sources: prev?.sources ?? [], web: null, kind: "draft" }));
          setPending({ threadId: d.thread_id });
          setNotice("Your sources may not fully answer this. Search the web?");
        }
        else if (event === "answered") {
          const d = data as AskResponse;
          setResult({ answer: d.answer ?? "", sources: d.sources ?? [], web: null, kind: "sources" });
          setNotice("Answered from your sources. No web search needed (0 credits).");
        }
      });
    }
    catch (error) { setResult(dropIfEmpty); setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsResearching(false); setIsStreaming(false); }
  }
  async function confirmSearch(approve: boolean) {
    if (!pending) return;
    const { threadId } = pending; setPending(null); setIsConfirming(true);
    if (approve) { setIsStreaming(true); setResult((prev) => ({ answer: "", sources: prev?.sources ?? [], web: null, kind: "web" })); setNotice("Planning one web search…"); }
    else setNotice("Using the draft from your sources…");
    let gotToken = false;
    try {
      await streamEvents("/confirm/stream", { thread_id: threadId, approve }, (event, data) => {
        if (event === "status") setNotice(data as string);
        else if (event === "token") { if (!gotToken) { gotToken = true; setNotice("Writing the verified answer…"); } setResult(appendToken(data as string)); }
        else if (event === "answered") {
          const d = data as AskResponse; const web = d.web_search ?? null;
          const verified = approve && !!web?.used && !web.error;
          setResult({ answer: stripSourcesFooter(d.answer ?? ""), sources: d.sources ?? [], web, kind: verified ? "web" : "unverified" });
          if (!approve) setNotice("Showing the unverified draft. No web search was made (0 credits).");
          else if (!verified) setNotice("Web search failed. Showing the unverified draft.");
          else if (web?.from_cache) setNotice("Answered with cached web results (0 credits).");
          else setNotice("Answered with 1 web search (1 credit). Review the cited sources below.");
        }
      });
    }
    catch (error) { setResult(dropIfEmpty); setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsConfirming(false); setIsStreaming(false); }
  }
  // Web fallback OFF: live token streaming from your sources only.
  async function streamAnswer() {
    setIsResearching(true); setIsStreaming(true); setNotice("Retrieving evidence…");
    setResult({ answer: "", sources: [], web: null, kind: "sources" });
    try {
      await streamEvents("/api/query/stream", { question }, (event, data) => {
        if (event === "sources") { const sources = data as string[]; setResult((prev) => ({ ...(prev ?? { answer: "", kind: "sources" }), sources })); setNotice("Writing answer…"); }
        else if (event === "token") setResult(appendToken(data as string));
        else if (event === "done") setNotice("Analysis ready. Review the cited source set below.");
      });
    }
    catch (error) { setResult(dropIfEmpty); setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsResearching(false); setIsStreaming(false); }
  }
  return <main>
    <nav><a className="brand" href="#top"><span>✦</span> FinSight</a><div className="nav-status"><i /> LOCAL INTELLIGENCE</div><button className="ghost">Research library</button></nav>
    <section className="hero" id="top"><p className="eyebrow">AI-POWERED FINANCIAL RESEARCH</p><h1>Clarity for every<br /><em>market-moving</em> question.</h1><p className="lede">Turn your selected financial reporting into defensible, source-backed analysis — entirely on your machine.</p><div className="metrics"><div><b>Private</b><span>local models</span></div><div><b>Grounded</b><span>source evidence</span></div><div><b>Fast</b><span>FAISS retrieval</span></div></div></section>
    <section className="workspace"><div className="source-panel"><div className="panel-heading"><div><p className="eyebrow">01 / BUILD CONTEXT</p><h2>Research collection</h2></div><span className="badge">LOCAL</span></div><form onSubmit={ingestSources}>{urls.map((url, index) => <label className="url-field" key={index}><span>0{index + 1}</span><input type="url" value={url} onChange={(e) => updateUrl(index, e.target.value)} placeholder="Paste a news or market analysis URL" /></label>)}<button className="primary" disabled={isIndexing}>{isIndexing ? "Indexing sources…" : "Create research collection"}<span>→</span></button></form></div>
      <div className="analysis-panel"><div className="terminal-heading"><div><p className="eyebrow">02 / ASK FINSIGHT</p><h2>Research terminal</h2></div><label className="web-toggle" title="When your sources can't answer, offer one web search (1 Tavily credit). Off = live-streamed answers from your sources only."><input type="checkbox" checked={webFallback} onChange={(e) => setWebFallback(e.target.checked)} disabled={isResearching || isConfirming} /><span className="track" aria-hidden="true" /><span className="toggle-text">WEB FALLBACK<small>{webFallback ? "asks before searching" : "sources only · live stream"}</small></span></label></div>
        <form onSubmit={askResearch} className="ask"><textarea value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What is the key risk to the company’s outlook?" /><button aria-label="Run research" disabled={isResearching || isConfirming}>{isResearching ? "…" : "↑"}</button></form><div className="notice"><span className="pulse" />{notice}</div>
        {result && <article className="answer" aria-live="polite"><p className="answer-label">SYNTHESIS {resultTag(result)}</p><p className={`answer-text${result.kind === "draft" ? " draft" : ""}`}>{result.answer}{isStreaming && <span className="cursor" aria-hidden="true" />}</p>
          {pending && !isStreaming && <div className="confirm-row"><p>Search the web for a verified answer?<small>Runs one search · uses 1 Tavily credit (0 if cached)</small></p><div className="confirm-actions"><button className="search-btn" onClick={() => confirmSearch(true)} disabled={isConfirming}>Search the web<span>↗</span></button><button className="draft-btn" onClick={() => confirmSearch(false)} disabled={isConfirming}>No, use draft</button></div></div>}
          {result.kind === "web" && result.web?.used && <p className="search-meta">Searched: “{result.web.query}” · {result.web.topic}{result.web.time_range ? ` · past ${result.web.time_range}` : ""}</p>}
          {result.sources.length > 0 && <><p className="answer-label">EVIDENCE</p><ul>{result.sources.map((source, i) => <li key={source}><a href={source} target="_blank" rel="noreferrer">{result.kind === "web" && result.web?.used ? `[${i + 1}] ` : ""}{hostname(source)} ↗</a></li>)}</ul></>}</article>}</div></section>
    <section className="future"><div><p className="eyebrow">ON THE HORIZON</p><h2>From research assistant<br />to <em>market intelligence desk.</em></h2></div><div className="feature-grid"><article><span>◌</span><h3>Live web discovery</h3><p>Agent-led searching across trusted financial sources, launched from a single research brief.</p><small>COMING SOON</small></article><article><span>⌁</span><h3>Multi-tool agents</h3><p>Specialist agents that compare filings, news, earnings calls, and market signals before they respond.</p><small>PLANNED</small></article><article><span>⌘</span><h3>Continuous watchlists</h3><p>Monitor companies and themes, then surface only developments that materially change the thesis.</p><small>PLANNED</small></article></div></section>
    <footer><span>FINSIGHT / LOCAL RESEARCH TERMINAL</span><span>BUILT FOR DELIBERATE DECISIONS</span></footer>
  </main>;
}

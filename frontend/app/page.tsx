"use client";

import { FormEvent, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type ResearchResult = { answer: string; sources: string[] };

export default function Dashboard() {
  const [urls, setUrls] = useState(["", "", ""]);
  const [question, setQuestion] = useState("");
  const [isIndexing, setIsIndexing] = useState(false);
  const [isResearching, setIsResearching] = useState(false);
  const [notice, setNotice] = useState("Bring your sources. We’ll ground every answer in them.");
  const [result, setResult] = useState<ResearchResult | null>(null);
  const updateUrl = (index: number, value: string) => setUrls((current) => current.map((url, i) => i === index ? value : url));
  async function ingestSources(event: FormEvent) {
    event.preventDefault(); const sources = urls.map((url) => url.trim()).filter(Boolean);
    if (!sources.length) return setNotice("Add at least one public article URL to create a collection.");
    setIsIndexing(true); setResult(null); setNotice("Reading articles and building your private research index…");
    try { const response = await fetch(`${API_URL}/api/process`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls: sources }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail ?? "Indexing failed."); setNotice(`${data.indexed_documents} research passages indexed from ${data.sources.length} source${data.sources.length === 1 ? "" : "s"}.`); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsIndexing(false); }
  }
  async function askResearch(event: FormEvent) {
    event.preventDefault(); if (!question.trim()) return; setIsResearching(true); setNotice("Synthesizing an evidence-led answer…");
    try { const response = await fetch(`${API_URL}/api/query`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail ?? "Research failed."); setResult(data); setNotice("Analysis ready. Review the cited source set below."); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Could not reach the research API."); } finally { setIsResearching(false); }
  }
  return <main>
    <nav><a className="brand" href="#top"><span>✦</span> FinSight</a><div className="nav-status"><i /> LOCAL INTELLIGENCE</div><button className="ghost">Research library</button></nav>
    <section className="hero" id="top"><p className="eyebrow">AI-POWERED FINANCIAL RESEARCH</p><h1>Clarity for every<br /><em>market-moving</em> question.</h1><p className="lede">Turn your selected financial reporting into defensible, source-backed analysis — entirely on your machine.</p><div className="metrics"><div><b>Private</b><span>local models</span></div><div><b>Grounded</b><span>source evidence</span></div><div><b>Fast</b><span>FAISS retrieval</span></div></div></section>
    <section className="workspace"><div className="source-panel"><div className="panel-heading"><div><p className="eyebrow">01 / BUILD CONTEXT</p><h2>Research collection</h2></div><span className="badge">LOCAL</span></div><form onSubmit={ingestSources}>{urls.map((url, index) => <label className="url-field" key={index}><span>0{index + 1}</span><input type="url" value={url} onChange={(e) => updateUrl(index, e.target.value)} placeholder="Paste a news or market analysis URL" /></label>)}<button className="primary" disabled={isIndexing}>{isIndexing ? "Indexing sources…" : "Create research collection"}<span>→</span></button></form></div>
      <div className="analysis-panel"><div><p className="eyebrow">02 / ASK FINSIGHT</p><h2>Research terminal</h2></div><form onSubmit={askResearch} className="ask"><textarea value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What is the key risk to the company’s outlook?" /><button aria-label="Run research" disabled={isResearching}>{isResearching ? "…" : "↑"}</button></form><div className="notice"><span className="pulse" />{notice}</div>{result && <article className="answer"><p className="answer-label">SYNTHESIS</p><p>{result.answer}</p>{result.sources.length > 0 && <><p className="answer-label">EVIDENCE</p><ul>{result.sources.map((source) => <li key={source}><a href={source} target="_blank" rel="noreferrer">{new URL(source).hostname.replace("www.", "")} ↗</a></li>)}</ul></>}</article>}</div></section>
    <section className="future"><div><p className="eyebrow">ON THE HORIZON</p><h2>From research assistant<br />to <em>market intelligence desk.</em></h2></div><div className="feature-grid"><article><span>◌</span><h3>Live web discovery</h3><p>Agent-led searching across trusted financial sources, launched from a single research brief.</p><small>COMING SOON</small></article><article><span>⌁</span><h3>Multi-tool agents</h3><p>Specialist agents that compare filings, news, earnings calls, and market signals before they respond.</p><small>PLANNED</small></article><article><span>⌘</span><h3>Continuous watchlists</h3><p>Monitor companies and themes, then surface only developments that materially change the thesis.</p><small>PLANNED</small></article></div></section>
    <footer><span>FINSIGHT / LOCAL RESEARCH TERMINAL</span><span>BUILT FOR DELIBERATE DECISIONS</span></footer>
  </main>;
}

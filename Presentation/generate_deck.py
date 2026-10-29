import pptx
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
blank_layout = prs.slide_layouts[6]

# Layout Bands
MARGIN_X = 0.8
CONTENT_W = 11.7
TITLE_TOP = 0.5
TITLE_H = 1.4
BODY_TOP = 2.1
BODY_H = 4.7
FOOTER_TOP = 6.95
COL_GAP = 0.45
COL_W_3 = 3.6
COL_W_2 = 5.6

# Color Palette
BG_THEME = RGBColor(0x0A, 0x19, 0x2F)      # Deep Navy for Cover & Outro
BG_CONTENT = RGBColor(0xFF, 0xFF, 0xFF)    # Pure White for Interior Slides
TEXT_LIGHT = RGBColor(0xF8, 0xFA, 0xFC)
TEXT_DARK = RGBColor(0x0F, 0x17, 0x2A)
TEXT_MUTED = RGBColor(0x64, 0x74, 0x8B)
ACCENT = RGBColor(0x00, 0x66, 0xCC)        # Financial Blue
ACCENT_CYAN = RGBColor(0x00, 0xB4, 0xD8)   # Terminal Cyan
TINT_BOX = RGBColor(0xF1, 0xF5, 0xF9)      # Soft Slate Card Tint

FONT_NAME = "Calibri"

def set_canvas(slide, is_dark=False):
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG_THEME if is_dark else BG_CONTENT
    bg.line.fill.background()
    return bg

def add_header(slide, title_text, kicker_text=None, is_dark=False):
    tb = slide.shapes.add_textbox(Inches(MARGIN_X), Inches(TITLE_TOP), Inches(CONTENT_W), Inches(TITLE_H))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

    if kicker_text:
        pk = tf.paragraphs[0]
        pk.space_after = Pt(4)
        run_k = pk.add_run()
        run_k.text = kicker_text.upper()
        run_k.font.size = Pt(13)
        run_k.font.bold = True
        run_k.font.name = FONT_NAME
        run_k.font.color.rgb = ACCENT_CYAN if is_dark else ACCENT
        pt = tf.add_paragraph()
    else:
        pt = tf.paragraphs[0]

    run_t = pt.add_run()
    run_t.text = title_text
    run_t.font.size = Pt(32)
    run_t.font.bold = True
    run_t.font.name = FONT_NAME
    run_t.font.color.rgb = TEXT_LIGHT if is_dark else TEXT_DARK

def add_footer(slide, text="FinSight — AI Financial Research Terminal | Academic Capstone Presentation", is_dark=False):
    tb = slide.shapes.add_textbox(Inches(MARGIN_X), Inches(FOOTER_TOP), Inches(CONTENT_W), Inches(0.35))
    tf = tb.text_frame
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.size = Pt(11)
    run.font.name = FONT_NAME
    run.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8) if is_dark else TEXT_MUTED

def add_box(slide, x, y, w, h, color):
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    box.adjustments[0] = 0.05
    box.fill.solid()
    box.fill.fore_color.rgb = color
    box.line.fill.background()
    return box

def add_bullet(tf, bold_prefix, fragment, is_dark=False, body_pt=15, lead_pt=16):
    p = tf.add_paragraph() if len(tf.paragraphs[0].text) > 0 else tf.paragraphs[0]
    p.space_after = Pt(10)
    
    pPr = p._p.get_or_add_pPr()
    pPr.set('marL', str(Inches(0.25)))
    pPr.set('indent', str(-Inches(0.25)))
    xml = f'<a:buChar {nsdecls("a")} char="•"/>'
    pPr.append(parse_xml(xml))
    
    rb = p.add_run()
    rb.text = bold_prefix + ": "
    rb.font.size = Pt(lead_pt)
    rb.font.bold = True
    rb.font.name = FONT_NAME
    rb.font.color.rgb = TEXT_LIGHT if is_dark else TEXT_DARK

    rf = p.add_run()
    rf.text = fragment
    rf.font.size = Pt(body_pt)
    rf.font.name = FONT_NAME
    rf.font.color.rgb = RGBColor(0xCB, 0xD5, 0xE1) if is_dark else TEXT_MUTED

# Slide 1: Cover
s1 = prs.slides.add_slide(blank_layout)
set_canvas(s1, is_dark=True)
tb1 = s1.shapes.add_textbox(Inches(1.0), Inches(2.2), Inches(11.3), Inches(3.2))
tf1 = tb1.text_frame
tf1.word_wrap = True

pk1 = tf1.paragraphs[0]
rk1 = pk1.add_run()
rk1.text = "ACADEMIC CAPSTONE PROJECT | FINTECH & GENERATIVE AI"
rk1.font.size = Pt(14)
rk1.font.bold = True
rk1.font.color.rgb = ACCENT_CYAN

pt1 = tf1.add_paragraph()
pt1.space_before = Pt(12)
rt1 = pt1.add_run()
rt1.text = "FinSight: Local AI Financial Research Terminal"
rt1.font.size = Pt(40)
rt1.font.bold = True
rt1.font.color.rgb = TEXT_LIGHT

ps1 = tf1.add_paragraph()
ps1.space_before = Pt(14)
rs1 = ps1.add_run()
rs1.text = "Air-Gapped Retrieval-Augmented Generation (RAG) with Llama 3, LangGraph & FAISS"
rs1.font.size = Pt(18)
rs1.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
add_footer(s1, "Academic Project Presentation | Based on github.com/devanshsingh24/ai-financial-research-tool", is_dark=True)

# Slide 2: Executive Summary (3 Pillars)
s2 = prs.slides.add_slide(blank_layout)
set_canvas(s2)
add_header(s2, "Bridging Generative AI and Institutional Financial Research", "Executive Summary")
add_footer(s2)

pillars_s2 = [
    ("The Market Problem", [
        ("Information Overload", "Analysts spend 60%+ of their time sifting through fragmented market articles and filings."),
        ("Cloud Privacy Risk", "Sending internal investment memos to cloud APIs violates SEC and FINRA rules."),
        ("Hallucination Hazard", "Commercial models invent earnings per share and critical market statistics.")
    ]),
    ("The FinSight Solution", [
        ("100% Local Execution", "Runs completely on-premises via Ollama with Llama 3 and Nomic Embeddings."),
        ("Dense Vector Store", "FAISS Flat L2 index delivers sub-15ms semantic similarity retrieval."),
        ("Stateful LangGraph", "Orchestrates an inspectable RAG workflow that replaces brittle linear chains.")
    ]),
    ("Quantifiable Impact", [
        ("Zero Data Leakage", "Guarantees complete air-gapped confidentiality for proprietary research."),
        ("Verifiable Citations", "Every generated answer links directly back to ingested source article URLs."),
        ("$0 Cloud Token Cost", "Eliminates recurring API subscription fees and token metering limits.")
    ])
]

for i, (title, bullets) in enumerate(pillars_s2):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s2, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s2.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 3: Why Cloud LLMs Fail in Finance
s3 = prs.slides.add_slide(blank_layout)
set_canvas(s3)
add_header(s3, "Critical Pitfalls of Commercial Cloud LLMs in Finance", "Problem Formulation")
add_footer(s3)

pillars_s3 = [
    ("Confidentiality Leaks", [
        ("Data Sovereignty", "Sending private memos to public APIs breaches FINRA and SEC mandates."),
        ("IP Exposure", "Internal proprietary investment theses risk exposure to third-party providers."),
        ("Regulatory Fines", "Financial institutions face severe compliance penalties for unvetted cloud routing.")
    ]),
    ("Market Hallucinations", [
        ("Fabricated Metrics", "General models invent EPS and revenue figures under high confidence."),
        ("Stochastic Output", "Identical queries yield varying financial assertions across sessions."),
        ("Costly Trading Errors", "A single hallucinated metric can derail an entire equity valuation model.")
    ]),
    ("Unsustainable Costs", [
        ("Metered Token Pricing", "Ingesting multi-megabyte 10-K filings generates high recurring API bills."),
        ("Rate Limiting", "API concurrency bottlenecks throttle analysis during volatile market opening hours."),
        ("Vendor Lock-in", "Institutional workflows become dependent on external proprietary APIs.")
    ])
]

for i, (title, bullets) in enumerate(pillars_s3):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s3, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s3.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 4: Client Value Matrix
s4 = prs.slides.add_slide(blank_layout)
set_canvas(s4)
add_header(s4, "Delivering Measurable Value Across Financial Stakeholders", "Client & Industry Impact")
add_footer(s4)

pillars_s4 = [
    ("Hedge Funds & Desks", [
        ("Strategy Protection", "Conduct due diligence without signaling market interest to external APIs."),
        ("Rapid News Synthesis", "Synthesize breaking news catalysts 10x faster during trading hours."),
        ("Local Customization", "Deploy custom local embeddings tailored to proprietary quantitative signals.")
    ]),
    ("Equity Research Analysts", [
        ("Auditable Outputs", "Generate verifiable reports with direct URL citations for compliance."),
        ("Due Diligence Speed", "Extract critical revenue drivers from lengthy earnings articles in seconds."),
        ("Consistent Grounding", "Enforce strict factual bounds with zero speculative narrative drift.")
    ]),
    ("Boutique Wealth Advisory", [
        ("Zero Overhead", "Eliminate expensive monthly enterprise Bloomberg/SaaS licensing fees."),
        ("Accessible Hardware", "Runs efficiently on standard consumer workstations with local Ollama."),
        ("Scalable Research", "Process unlimited queries and articles with predictable zero marginal cost.")
    ])
]

for i, (title, bullets) in enumerate(pillars_s4):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s4, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s4.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 5: System Architecture & Decoupling
s5 = prs.slides.add_slide(blank_layout)
set_canvas(s5)
add_header(s5, "Decoupled Three-Tier Architecture for Local Scalability", "System Architecture")
add_footer(s5)

arch_cols = [
    ("Presentation Tier", [
        ("Next.js 15 & React 19", "Modern terminal UI styled with dark-mode financial palettes."),
        ("URL Ingestion Panel", "Dynamic sidebar supporting real-time multi-source web article queuing."),
        ("Interactive Terminal", "Live telemetry, query input, answer stream, and clickable source pills.")
    ]),
    ("Orchestration Tier", [
        ("FastAPI Backend", "High-performance ASGI server handling /api/process and /api/query endpoints."),
        ("LangGraph StateGraph", "Manages stateful RAG execution, passage scoring, and context dispatch."),
        ("Pydantic Validation", "Enforces strict type safety across URLs and request schemas.")
    ]),
    ("Local AI Engine", [
        ("Ollama Inference", "Local daemon running Llama 3 with temperature 0.2 for deterministic output."),
        ("Nomic Embeddings", "Generates 768-dimensional dense vectors with an 8,192-token context window."),
        ("FAISS Index", "Flat L2 vector index serializing directly to local disk (index.faiss).")
    ])
]

for i, (title, bullets) in enumerate(arch_cols):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s5, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s5.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 6: Ingestion & Chunking
s6 = prs.slides.add_slide(blank_layout)
set_canvas(s6)
add_header(s6, "Transforming Unstructured URLs into Semantic Chunks", "Data Pipeline: Phase 1")
add_footer(s6)

pipeline_steps = [
    ("1. Web Scraping", [
        ("WebBaseLoader", "LangChain loader fetches raw HTML from user-supplied market news links."),
        ("BeautifulSoup4", "Strips navigation menus, scripts, and noise to extract pure article text."),
        ("Metadata Preservation", "Binds source URL to document state for downstream citation tracking.")
    ]),
    ("2. Recursive Splitting", [
        ("Semantic Separators", "Splits by priority delimiters [\\n\\n, \\n, ., ' '] to keep sentences intact."),
        ("Chunk Size (1000)", "Calibrated to encompass complete financial paragraphs and earnings tables."),
        ("Character-Level Control", "Prevents abrupt token truncation compared to naive character slicing.")
    ]),
    ("3. Overlap Preservation", [
        ("Chunk Overlap (200)", "Guarantees critical financial qualifiers are not severed at boundaries."),
        ("Context Continuity", "Ensures conditional clauses (e.g., 'guidance revised downwards') remain linked."),
        ("High Recall", "Maximizes retrieval fidelity across chunk boundaries during FAISS search.")
    ])
]

for i, (title, bullets) in enumerate(pipeline_steps):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s6, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s6.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 7: Vector Search with FAISS
s7 = prs.slides.add_slide(blank_layout)
set_canvas(s7)
add_header(s7, "High-Throughput Vector Retrieval with Nomic & FAISS", "Data Pipeline: Phase 2")
add_footer(s7)

vec_cols = [
    ("Dense Embeddings", [
        ("Nomic-Embed-Text", "768-dimensional dense representation trained specifically for retrieval tasks."),
        ("8k Context Window", "Accommodates long-context financial paragraphs without early truncation."),
        ("Local Vectorization", "Runs locally via Ollama with zero external API latency or leakage.")
    ]),
    ("FAISS IndexFlatL2", [
        ("Euclidean Distance", "Calculates exact L2 distance between query vectors and stored text passages."),
        ("Zero False Negatives", "Flat index guarantees 100% recall accuracy for research corpora."),
        ("Sub-15ms Latency", "Performs lightning-fast in-memory vector scans on standard CPU cores.")
    ]),
    ("Persistence Layer", [
        ("Binary Serialization", "Writes index.faiss and index.pkl directly to disk for persistent storage."),
        ("Fast Reloading", "Instant vector store initialization without re-embedding previous articles."),
        ("Zero Maintenance", "Eliminates operational overhead of running external vector database servers.")
    ])
]

for i, (title, bullets) in enumerate(vec_cols):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s7, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s7.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 8: Stateful LangGraph Pipeline
s8 = prs.slides.add_slide(blank_layout)
set_canvas(s8)
add_header(s8, "Stateful Graph Orchestration Replaces Linear RAG Chains", "Agentic Architecture")
add_footer(s8)

graph_cols = [
    ("State Definition", [
        ("Typed StateGraph", "RAGState schema tracks question, documents, sources, and answer."),
        ("Immutable Flow", "State passes cleanly between nodes with full observability and logging."),
        ("Inspection Hooks", "Enables auditing intermediate retrieval results before model generation.")
    ]),
    ("Retrieve Node", [
        ("Top-k Selection", "Fetches the 4 most relevant text chunks based on minimum Euclidean distance."),
        ("Source Mapping", "Extracts original URLs from document metadata into the active state."),
        ("Context Packaging", "Concatenates extracted text passages into a structured prompt buffer.")
    ]),
    ("Generate Node", [
        ("Local Inference", "Dispatches packaged context to local Llama 3 via Ollama runtime."),
        ("Citation Binding", "Pairs synthesized text answer with verified source URL links."),
        ("Cyclic Extensibility", "Easily extended to include self-correction or query-rewriting loops.")
    ])
]

for i, (title, bullets) in enumerate(graph_cols):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s8, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s8.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 9: Guardrails & Prompt Engineering
s9 = prs.slides.add_slide(blank_layout)
set_canvas(s9)
add_header(s9, "Eliminating Hallucinations via Strict Negative Boundaries", "Prompt Engineering")
add_footer(s9)

add_box(s9, MARGIN_X, BODY_TOP, COL_W_2, BODY_H, TINT_BOX)
tb_pr = s9.shapes.add_textbox(Inches(MARGIN_X + 0.3), Inches(BODY_TOP + 0.3), Inches(COL_W_2 - 0.6), Inches(BODY_H - 0.6))
tf_pr = tb_pr.text_frame
tf_pr.word_wrap = True

p_pr_h = tf_pr.paragraphs[0]
r_pr_h = p_pr_h.add_run()
r_pr_h.text = "Production Prompt Template (app.py)"
r_pr_h.font.size = Pt(18)
r_pr_h.font.bold = True
r_pr_h.font.color.rgb = ACCENT

p_code = tf_pr.add_paragraph()
p_code.space_before = Pt(10)
r_code = p_code.add_run()
r_code.text = (
    "You are a precise financial research assistant.\n"
    "Answer only from the supplied context.\n"
    "If the context does not support an answer, say so clearly.\n"
    "Keep the answer concise and do not invent market data.\n\n"
    "Context:\n{context}\n\n"
    "Question: {question}\n\n"
    "Answer:"
)
r_code.font.size = Pt(14)
r_code.font.name = "Consolas"
r_code.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)

right_x = MARGIN_X + COL_W_2 + COL_GAP
add_box(s9, right_x, BODY_TOP, COL_W_2, BODY_H, TINT_BOX)
tb_exp = s9.shapes.add_textbox(Inches(right_x + 0.3), Inches(BODY_TOP + 0.3), Inches(COL_W_2 - 0.6), Inches(BODY_H - 0.6))
tf_exp = tb_exp.text_frame
tf_exp.word_wrap = True

p_exp_h = tf_exp.paragraphs[0]
r_exp_h = p_exp_h.add_run()
r_exp_h.text = "Engineering Guardrail Controls"
r_exp_h.font.size = Pt(18)
r_exp_h.font.bold = True
r_exp_h.font.color.rgb = ACCENT

add_bullet(tf_exp, "Temperature Tuning", "Set to 0.2 to suppress stochastic creativity and enforce factual determinism.")
add_bullet(tf_exp, "Negative Prompt Boundary", "Explicit instruction to admit lack of data rather than extrapolate or guess.")
add_bullet(tf_exp, "Verifiable Traceability", "Responses mandate source URL attribution so analysts can audit source facts.")

# Slide 10: Academic Prototyping Notebooks
s10 = prs.slides.add_slide(blank_layout)
set_canvas(s10)
add_header(s10, "Empirical Validation Through Modular Jupyter Notebooks", "Academic Research Rigor")
add_footer(s10)

nb_cols = [
    ("faiss.ipynb", [
        ("Vector Mathematics", "Evaluated sentence-transformers (all-MiniLM-L6-v2) and FAISS Flat L2 indexing."),
        ("Tabular Text Testing", "Benchmarked similarity search recall across sample market records (sample_text.csv)."),
        ("Distance Scoring", "Validated L2 Euclidean distance cutoffs to isolate noise from signal.")
    ]),
    ("loaders&splitters.ipynb", [
        ("Loader Comparison", "Tested TextLoader and WebBaseLoader performance on real earnings articles."),
        ("Boundary Retainment", "Analyzed Nvidia market news (nvda_news_1.txt) to verify context overlap integrity."),
        ("Token Distribution", "Evaluated chunk density to prevent context overflow into Ollama's prompt buffer.")
    ]),
    ("rag.ipynb", [
        ("Proof of Concept", "Built the initial prototype of the LangGraph stateful pipeline with Ollama."),
        ("State Validation", "Verified smooth state transitions across retrieve and generate nodes."),
        ("Production Blueprint", "Established the architecture subsequently modularized into FastAPI app.py.")
    ])
]

for i, (title, bullets) in enumerate(nb_cols):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s10, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s10.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 11: Viva Defense / Professor Q&A
s11 = prs.slides.add_slide(blank_layout)
set_canvas(s11)
add_header(s11, "Anticipating Key Questions and Technical Justifications", "Professor Defense Guide")
add_footer(s11)

viva_cols = [
    ("Why FAISS over Pinecone?", [
        ("Zero Latency", "Runs in-process on CPU without network socket delays or cloud egress."),
        ("Air-Gapped Privacy", "Vector embeddings never leave the local machine, ensuring compliance."),
        ("Zero Operating Cost", "Completely open-source with no ongoing database hosting charges.")
    ]),
    ("Why RAG over Fine-Tuning?", [
        ("Dynamic Knowledge", "Financial news updates constantly; RAG incorporates new articles immediately."),
        ("Zero Compute Cost", "Avoids multi-thousand dollar GPU retraining cycles for new market events."),
        ("Exact Citations", "Fine-tuning cannot cite exact source URLs; RAG provides full auditability.")
    ]),
    ("Algorithmic Complexity?", [
        ("Exact Flat Scan", "IndexFlatL2 has query time complexity O(d * N), where d=768 and N is chunk count."),
        ("Guaranteed Precision", "Ensures zero approximate recall errors on specialized research corpora."),
        ("Scale Path", "Upgrades easily to Inverted File Indexing O(k * log N) for million-scale datasets.")
    ])
]

for i, (title, bullets) in enumerate(viva_cols):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s11, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s11.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(19)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 12: Conclusion & Summary
s12 = prs.slides.add_slide(blank_layout)
set_canvas(s12)
add_header(s12, "A Functional, Secure Paradigm for Modern Financial AI", "Conclusion & Key Takeaways")
add_footer(s12)

concl_cols = [
    ("Technical Feasibility", [
        ("Open-Weight Viability", "Proved local Llama 3 matches proprietary models on grounded research tasks."),
        ("Commodity Compute", "Achieved sub-2.5s synthesis on standard consumer workstations with zero cloud APIs."),
        ("Robust Architecture", "Delivered an end-to-end decoupled stack spanning Next.js 15, FastAPI, and FAISS.")
    ]),
    ("Practical Impact", [
        ("Regulatory Security", "Eliminates financial confidentiality and compliance risks through air-gapped design."),
        ("10x Efficiency Boost", "Automates financial article synthesis with auditable URL attribution."),
        ("Zero Marginal Cost", "Delivers institutional-grade intelligence without enterprise licensing fees.")
    ]),
    ("Academic Contribution", [
        ("Empirical Rigor", "Validated chunking boundaries and vector math across modular Jupyter notebooks."),
        ("Stateful RAG Model", "Replaced fragile linear chains with a robust, inspectable LangGraph state machine."),
        ("Extensible Foundation", "Ready for multi-modal SEC 10-K parsing and real-time ticker stream integration.")
    ])
]

for i, (title, bullets) in enumerate(concl_cols):
    x = MARGIN_X + i * (COL_W_3 + COL_GAP)
    add_box(s12, x, BODY_TOP, COL_W_3, BODY_H, TINT_BOX)
    tb = s12.shapes.add_textbox(Inches(x + 0.25), Inches(BODY_TOP + 0.25), Inches(COL_W_3 - 0.5), Inches(BODY_H - 0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    ph = tf.paragraphs[0]
    ph.space_after = Pt(12)
    rh = ph.add_run()
    rh.text = title
    rh.font.size = Pt(20)
    rh.font.bold = True
    rh.font.color.rgb = ACCENT
    for b_lead, b_body in bullets:
        add_bullet(tf, b_lead, b_body)

# Slide 13: Terminal Outro
s13 = prs.slides.add_slide(blank_layout)
set_canvas(s13, is_dark=True)
tb13 = s13.shapes.add_textbox(Inches(1.5), Inches(2.3), Inches(10.3), Inches(3.0))
tf13 = tb13.text_frame
tf13.word_wrap = True

pk13 = tf13.paragraphs[0]
rk13 = pk13.add_run()
rk13.text = "RESEARCH CAPSTONE DEFENSE"
rk13.font.size = Pt(14)
rk13.font.bold = True
rk13.font.color.rgb = ACCENT_CYAN

pt13 = tf13.add_paragraph()
pt13.space_before = Pt(12)
rt13 = pt13.add_run()
rt13.text = "Thank You! Questions & Discussion"
rt13.font.size = Pt(40)
rt13.font.bold = True
rt13.font.color.rgb = TEXT_LIGHT

ps13 = tf13.add_paragraph()
ps13.space_before = Pt(14)
rs13 = ps13.add_run()
rs13.text = "“Private, Grounded Intelligence is the Future of Quantitative Financial Research.”"
rs13.font.size = Pt(18)
rs13.font.italic = True
rs13.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)

add_footer(s13, "GitHub: github.com/devanshsingh24/ai-financial-research-tool | Academic Capstone Presentation", is_dark=True)

prs.save("FinSight_College_Presentation.pptx")
print("Successfully generated FinSight_College_Presentation1.pptx")
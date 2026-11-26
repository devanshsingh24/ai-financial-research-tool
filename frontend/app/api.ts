// Shared API client: base URL, the X-User-Id header, SSE reading and response types.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type WebSearchInfo = { used: boolean; from_cache: boolean; query: string; topic: string; time_range: string | null; error: string | null };
export type SearchPlan = { query: string; topic: string; time_range: string | null };
export type GateKind = "web_search" | "report";
export type GateAction = "approve" | "reject" | "edit";
export type AskResponse = { status: "answered" | "needs_confirmation"; thread_id: string; kind?: GateKind | null; prompt?: string | null; draft?: string | null; search?: SearchPlan | null; answer?: string | null; sources?: string[]; web_search?: WebSearchInfo | null };
export type ThreadStatus = "idle" | "running" | "interrupted" | "completed" | "failed";
export type ThreadOut = { thread_id: string; user_id: string; title: string; created_at: string; updated_at: string; status: ThreadStatus; error: string | null };
export type ThreadState = { thread: ThreadOut; next: string[]; pending: AskResponse | null; last: AskResponse | null; messages: { role: "user" | "assistant"; content: string }[]; summary: string; memories: string[]; answer: string | null; checkpoint_id: string | null };
export type Checkpoint = { checkpoint_id: string; parent_id: string | null; step: number | null; source: string | null; next: string[]; created_at: string | null; values: Record<string, unknown> };

// No auth yet: the backend trusts this header to scope threads and memories to a user.
export const headers = (userId: string) => ({ "Content-Type": "application/json", "X-User-Id": userId });

// JSON request; throws with the backend's `detail` on errors.
export async function api<T>(path: string, userId: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { method: init.method ?? "GET", headers: headers(userId), body: init.body === undefined ? undefined : JSON.stringify(init.body) });
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed.");
  return data as T;
}

// POSTs to a Server-Sent-Events endpoint and calls onEvent for each event until the stream ends.
export async function streamEvents(path: string, body: unknown, userId: string, onEvent: (event: string, data: unknown) => void): Promise<void> {
  const response = await fetch(`${API_URL}${path}`, { method: "POST", headers: headers(userId), body: JSON.stringify(body) });
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
export function parseEvent(block: string): { event: string; data: unknown } | null {
  let event = "message"; let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch { return null; }
}

// The backend appends "Sources:\n[1] url…" to web answers; the UI lists sources itself.
export const stripSourcesFooter = (answer: string) => answer.replace(/\n\nSources:\n[\s\S]*$/, "");

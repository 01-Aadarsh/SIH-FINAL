import type { QueryRequest, QueryResponse } from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * The backend's own REQUEST_TIMEOUT defaults to 90s (see
 * docs/API_CONTRACT.md — up to 3 sequential LLM calls in the worst case:
 * chat-history rewrite, bounded-retry rewrite, generate) and returns a
 * clean 504 past that point. We set the client timeout a little above it
 * so the server's informative 504 wins the race instead of our fetch
 * aborting first with a generic error.
 */
const CLIENT_TIMEOUT_MS = 98_000;

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class ClientTimeoutError extends Error {
  constructor() {
    super(
      "The request took longer than 98 seconds without a response — the backend may be unreachable or overloaded."
    );
    this.name = "ClientTimeoutError";
  }
}

async function extractErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // FastAPI's 422 validation shape: [{ loc, msg, type, ... }, ...]
      return detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          d?.msg ? `${(d.loc ?? []).join(".")}: ${d.msg}` : JSON.stringify(d)
        )
        .join("; ");
    }
    return res.statusText || `Request failed with status ${res.status}`;
  } catch {
    return res.statusText || `Request failed with status ${res.status}`;
  }
}

export async function health(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

export async function query(req: QueryRequest): Promise<QueryResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: req.question,
        history: req.history ?? [],
        jurisdiction: req.jurisdiction ?? "india",
        language: req.language ?? "en-IN",
        synthesize_audio: req.synthesize_audio ?? false,
      }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ClientTimeoutError();
    }
    throw new ApiError(
      "Could not reach the backend. Is it running at " +
        API_BASE_URL +
        "?",
      0
    );
  } finally {
    clearTimeout(timeoutId);
  }

  if (!res.ok) {
    const detail = await extractErrorDetail(res);
    throw new ApiError(detail, res.status);
  }

  return (await res.json()) as QueryResponse;
}

/** A citation's source PDF, served by the backend's GET /sources/{filename}
 * mount (backend/api/main.py) — `#page=N` is honored by most browsers'
 * native PDF viewer. */
export function sourceUrl(sourceFile: string, page?: number): string {
  const base = `${API_BASE_URL}/sources/${encodeURIComponent(sourceFile)}`;
  return page ? `${base}#page=${page}` : base;
}

export { API_BASE_URL };

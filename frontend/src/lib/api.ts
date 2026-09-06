import type {
  QueryRequest,
  QueryResponse,
  StreamDoneData,
  TranscribeResponse,
} from "./types";

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

function buildQueryBody(req: QueryRequest) {
  return {
    question: req.question,
    history: req.history ?? [],
    jurisdiction: req.jurisdiction ?? "india",
    language: req.language ?? "en-IN",
    synthesize_audio: req.synthesize_audio ?? false,
  };
}

export async function query(req: QueryRequest): Promise<QueryResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildQueryBody(req)),
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

export interface StreamCallbacks {
  /** Called once per `token` SSE event, in order — append to the
   * in-progress answer as they arrive. */
  onToken: (text: string) => void;
  /** Called exactly once, when the `done` event arrives — the full
   * assembled answer plus citations/flags/etc. `answer` here is the same
   * text already delivered via onToken, not new content. */
  onDone: (data: StreamDoneData) => void;
}

/** One line of an SSE block, either "event: <name>" or "data: <json>" —
 * see backend/api/main.py::_sse. */
function parseSseBlock(block: string): { event: string | null; data: string } {
  let event: string | null = null;
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trim());
  }
  return { event, data: dataLines.join("\n") };
}

/**
 * Streams /query/stream token-by-token via a raw fetch + ReadableStream
 * reader (not EventSource, which can't send a POST body). Resolves once
 * the `done` event has been parsed and dispatched to `callbacks.onDone`;
 * throws (without ever having called onDone) if the connection drops or
 * the server sends an `error` event first — callers should catch that and
 * fall back to the non-streaming `query()`, since a partial token stream
 * with no `done` payload has no citations/flags to show.
 */
export async function queryStream(
  req: QueryRequest,
  callbacks: StreamCallbacks
): Promise<void> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildQueryBody(req)),
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timeoutId);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ClientTimeoutError();
    }
    throw new ApiError(
      "Could not reach the backend. Is it running at " + API_BASE_URL + "?",
      0
    );
  }

  if (!res.ok || !res.body) {
    clearTimeout(timeoutId);
    const detail = await extractErrorDetail(res);
    throw new ApiError(detail, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let doneReceived = false;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary: number;
      // SSE frames are separated by a blank line ("\n\n") — see
      // backend/api/main.py::_sse. A frame may arrive split across
      // multiple reader.read() calls, so only complete frames are
      // consumed here; the remainder stays in `buffer` for next time.
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const rawBlock = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const { event, data } = parseSseBlock(rawBlock);
        if (!event || !data) continue;

        if (event === "token") {
          const parsed = JSON.parse(data) as { text?: string };
          if (parsed.text) callbacks.onToken(parsed.text);
        } else if (event === "done") {
          doneReceived = true;
          callbacks.onDone(JSON.parse(data) as StreamDoneData);
        } else if (event === "error") {
          const parsed = JSON.parse(data) as { detail?: string };
          throw new ApiError(parsed.detail ?? "Streaming request failed.", 0);
        }
      }
    }
  } finally {
    clearTimeout(timeoutId);
  }

  if (!doneReceived) {
    throw new ApiError(
      "The stream ended before a final answer arrived — the connection may have dropped.",
      0
    );
  }
}

/** POST /api/v1/voice/transcribe — speech-to-text via Sarvam's Saaras
 * model (backend/api/asr.py). `languageCode` defaults to "unknown"
 * (auto-detect); accepts .wav/.mp3/.m4a/.webm. */
export async function transcribeAudio(
  audioBlob: Blob,
  filename: string,
  languageCode: string = "unknown"
): Promise<TranscribeResponse> {
  const form = new FormData();
  form.append("file", audioBlob, filename);
  form.append("language_code", languageCode);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/voice/transcribe`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ClientTimeoutError();
    }
    throw new ApiError(
      "Could not reach the backend for transcription. Is it running at " +
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

  return (await res.json()) as TranscribeResponse;
}

/** A citation's source PDF, served by the backend's GET /sources/{filename}
 * mount (backend/api/main.py) — `#page=N` is honored by most browsers'
 * native PDF viewer. */
export function sourceUrl(sourceFile: string, page?: number): string {
  const base = `${API_BASE_URL}/sources/${encodeURIComponent(sourceFile)}`;
  return page ? `${base}#page=${page}` : base;
}

export { API_BASE_URL };

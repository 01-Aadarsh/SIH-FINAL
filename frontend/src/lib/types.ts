// Kept in sync with backend/api/main.py's Pydantic models and
// docs/API_CONTRACT.md by hand — that doc is the source of truth for the
// real shape (backend/tests/test_api_contract.py enforces it server-side).

export type Jurisdiction = "india" | "international";

export const FORMULATION_CATEGORIES = [
  "Classical medicine",
  "Proprietary medicine",
  "New / non-classical drug",
  "Phytopharmaceutical",
  "Ayurveda-Aahar / nutraceutical",
  "Cosmetic",
] as const;

export type FormulationCategory = (typeof FORMULATION_CATEGORIES)[number];

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

/** Exactly what the backend's Citation model returns — no `text` or
 * per-citation `confidence` field exists there (see docs/API_CONTRACT.md).
 * The LLM never writes these; they're attached from the chunks that were
 * actually retrieved (generation/citation.py). */
export interface Citation {
  chunk_id: string;
  source_file: string;
  page_number: number;
  section_heading: string;
}

export interface Flags {
  abstained: boolean;
  retried: boolean;
  weak_grounding: boolean;
}

/** A knowledge-graph cross-reference (graph_kg/kg.py) — a pointer at a
 * real, separately-indexed chunk, never new generated text. */
export interface RelatedProvision {
  tag: string;
  relation: "cross_jurisdiction_counterpart" | "co_occurs_with";
  source_file: string;
  page_number: number;
  section_heading: string;
  jurisdiction: Jurisdiction;
}

/** An official government form/registry match (compliance/form_navigator.py) —
 * first-pass reference data, not verified against live government sources
 * on every field. */
export interface FormCard {
  form_id: string;
  agency: string;
  jurisdiction: Jurisdiction;
  title: string;
  statutory_mandate: string;
  submission_portal: string;
  required_attachments: string[];
  deadline: string;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  flags: Flags;
  formulation_category: string;
  confidence_score: number;
  audio_base64: string | null;
  related_provisions: RelatedProvision[];
  needs_clarification: boolean;
  clarifying_questions: string[];
  actionable_forms: FormCard[];
}

export interface QueryRequest {
  question: string;
  history?: ChatTurn[];
  jurisdiction?: Jurisdiction;
  /** BCP-47 code, e.g. "en-IN" / "hi-IN" — see docs/API_CONTRACT.md for
   * the full 23-code list. Omit for English (the backend's own default). */
  language?: string;
  synthesize_audio?: boolean;
}

/** The /query/stream `done` SSE event's payload — identical to
 * QueryResponse except audio_base64, which the streaming endpoint never
 * returns at all (see docs/API_CONTRACT.md: "streaming never synthesizes
 * audio — there's no synthesize_audio support on this endpoint"). */
export type StreamDoneData = Omit<QueryResponse, "audio_base64">;

/** POST /api/v1/voice/transcribe's response shape (backend/api/main.py). */
export interface TranscribeResponse {
  transcript: string;
  detected_language: string;
}

/** One entry in the on-screen conversation — a superset of ChatTurn that also
 * carries the full response so the UI can render each turn correctly. */
export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  flags?: Flags;
  formulation_category?: string;
  confidence_score?: number;
  related_provisions?: RelatedProvision[];
  needs_clarification?: boolean;
  clarifying_questions?: string[];
  actionable_forms?: FormCard[];
  /** Set when this assistant turn failed outright (network/5xx/422) instead
   * of returning a QueryResponse — rendered as an error bubble, never sent
   * back to the backend as history. */
  error?: string;
  pending?: boolean;
}

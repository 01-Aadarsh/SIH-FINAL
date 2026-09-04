"""
System prompt for IP-SAKTI's grounded generation step.

The prompt is the only thing standing between "answers only from retrieved
context" and a model that quietly falls back on pretraining knowledge. The
constraint is repeated in different words on purpose — a single soft
instruction is easy for an LLM to override when the retrieved context looks
thin but the question still sounds answerable from general knowledge.

The model never writes citations. citation.py attaches those from the chunks
that were actually retrieved, not from anything the model outputs.
"""

from __future__ import annotations

# Fixed prefix the model must use verbatim when abstaining, so the caller can
# detect abstention with a plain string check instead of parsing free text.
ABSTENTION_MARKER = "I could not find this in my sources."

SYSTEM_PROMPT = f"""You are IP-SAKTI Sahayak, an assistant that answers questions about \
Intellectual Property and regulatory guidance for Ayurveda in India, using ONLY the \
context provided below.

Rules, in order of priority:

1. Answer using ONLY the information in the "Context" section of the user \
message. Never use outside knowledge, training data, or general familiarity \
with the topic, even if you are confident the answer is correct. If a fact \
is not written in the context, you do not know it.

2. If the context does not contain enough information to answer the \
question, your response MUST begin with exactly this sentence: \
"{ABSTENTION_MARKER}" Then, on a new line, ask exactly one clarifying \
question that would help narrow the search (for example, naming a specific \
act, section, or topic). Do not guess, infer beyond what is stated, or fill \
the gap with outside knowledge.

3. Do not include citations, source names, page numbers, or bracketed \
references in your answer (no "[Source 1]", no "(see page 12)"). The system \
attaches real citations separately after your answer — your job is only the \
answer text itself.

4. You do not reliably know the page number, section heading, or source \
document for anything in the context — the "Context" text is raw document \
content only. Numbers that look like clause or section codes inside it \
(e.g. "08.03.05.15") are part of the source text, not a page number, and \
must never be presented as one. If the user directly asks which page, \
section, or document something is from, say that exact locators are shown \
in the Sources list attached to this answer, rather than guessing.

5. Be concise and direct. Do not pad the answer with disclaimers beyond what \
rule 2 (or rule 4, when it applies) requires.
"""


def build_user_prompt(
    query: str,
    chunks: list[dict],
    formulation_category: str | None = None,
    statutory_tags: list[str] | None = None,
) -> str:
    """Assemble the context block + question the model actually sees.

    formulation_category/statutory_tags (see graph/formulation.py) are
    advisory framing, not a hard constraint — they tell the model which
    regulatory lens the question likely falls under, so an answer about a
    proprietary Ayurvedic medicine doesn't accidentally read as though it
    applies to classical formulations or vice versa. They do not override
    rule 1 (answer only from the Context below): a heuristic category
    label is not itself something to cite or treat as ground truth.
    """
    if not chunks:
        context = "(no context was retrieved for this query)"
    else:
        context = "\n\n---\n\n".join(chunk["text"] for chunk in chunks)

    framing = ""
    if formulation_category:
        tags = ", ".join(statutory_tags or [])
        framing = (
            f"Likely regulatory category (heuristic, not confirmed by the user): "
            f"{formulation_category}. Statutory areas typically relevant to this "
            f"category: {tags}. Use this only to frame which regulatory angle the "
            f"question is probably about — never state it as a fact about the "
            f"user's specific product, and never let it substitute for what the "
            f"Context below actually says.\n\n"
        )

    return f"{framing}Context:\n{context}\n\nQuestion: {query}"

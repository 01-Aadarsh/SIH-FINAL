"""
Citation attachment for IP-SAKTI.

The LLM never writes citations — this module takes the chunks that were
actually retrieved (not anything the model said) and turns them into the
source list shown to the user. A citation here cannot be hallucinated
because it never passes through the model.
"""

from __future__ import annotations

import re

from generation.prompts import ABSTENTION_MARKER


def is_abstention(answer: str) -> bool:
    """True if the model used the fixed abstention prefix from prompts.py."""
    return answer.strip().startswith(ABSTENTION_MARKER)


# "Section 3(p)", "Article 3", "Rule 158B" — a statutory locator shaped
# reference. Deliberately not "Chapter"/"Schedule"/"Part": those are broader
# groupings a model could legitimately paraphrase ("the definitions
# chapter") without quoting a locator, so checking them would just produce
# false positives rather than a real signal.
_STATUTORY_REFERENCE_PATTERN = re.compile(
    r"\b(?:Section|Article|Rule)\s+\d+[A-Za-z]?(?:\(\w+\))*", re.I
)


def find_ungrounded_references(answer: str, chunks: list[dict]) -> list[str]:
    """
    Best-effort post-generation grounding check: every "Section N" / "Article
    N" / "Rule N"-shaped locator the model's answer names should appear
    somewhere in the actual retrieved text it was given (rule 1 in
    prompts.py: answer ONLY from the Context). If a locator the model named
    appears nowhere in the retrieved chunks' own text, that's a concrete,
    checkable sign the number may have come from pretraining rather than
    the Context, despite the prompt telling it not to.

    Deliberately NOT used to strip or rewrite the answer text: surgically
    removing "Section 15" from "...as described in Section 15 of the
    Act..." leaves an ungrammatical fragment, and there's no reliable way
    to tell whether the surrounding sentence still makes sense without it.
    Citations in this project are only ever attached from real retrieved
    chunks (see attach_citations above), never extracted from or edited
    into the model's own text — this check follows the same rule: it's a
    diagnostic for the caller to log or act on, not a text editor.

    Not exhaustive in the other direction either: correct prose can
    reference a section without ever repeating its exact "Section N"
    spelling (e.g. "the definition clause" instead of "Section 2(1)(zb)"),
    so an empty result here is not proof the whole answer is grounded —
    only that no locator-shaped claim it DID make is unverifiable this way.
    """
    if not answer or not chunks:
        return []

    context_text = " ".join(chunk.get("text", "") for chunk in chunks).lower()
    referenced = {
        " ".join(match.group(0).split())
        for match in _STATUTORY_REFERENCE_PATTERN.finditer(answer)
    }
    return sorted(ref for ref in referenced if ref.lower() not in context_text)


def attach_citations(chunks: list[dict]) -> list[dict]:
    """Build the source list from retrieved chunks, deduped by chunk_id.

    Takes only the chunks the retrieval pipeline actually returned — the
    model has no input into which chunks appear here or what their metadata
    says.
    """
    seen: set[str] = set()
    citations = []
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        citations.append(
            {
                "chunk_id": chunk_id,
                "source_file": chunk["source_file"],
                "page_number": chunk["page_number"],
                "section_heading": chunk["section_heading"],
            }
        )
    return citations

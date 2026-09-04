"""
Formulation-category triage for IP-SAKTI.

Classifies which regulatory posture a question is asking about — an
Ayurvedic product's IP/ABS/labeling obligations differ sharply depending on
whether it's a classical formulation, a proprietary (P&P) medicine, a
phytopharmaceutical, a nutraceutical (Ayurveda-Aahar), or a cosmetic — and
injects that category plus its statutory tags into the generation prompt so
the LLM answers with the right regulatory frame in view, not a generic one.

Deterministic on purpose, not an LLM call: this project's whole retry
mechanism exists because LLM-driven decisions (query rewriting) aren't
perfectly reproducible run to run — see graph/nodes.py's should_retry and
the empirical finding in idea.md. Classifying with another LLM call here
would reintroduce exactly that non-determinism one step earlier, before
retrieval even starts. A keyword classifier is a coarse instrument — it
will misclassify genuinely ambiguous or multi-category questions — but it
is at least the same coarse answer every time, which an LLM call is not
guaranteed to be even at temperature 0.

CATEGORY_STATUTORY_TAGS maps each category to a fixed vocabulary of
statutory tags. This is a heuristic FIRST PASS, not authoritative legal
categorization — see ingestion/chunker.py::tag_statutory_metadata for how
(and how unreliably) these tags actually get attached to indexed chunks.
Treat both the classifier and the tag map as something a domain expert
should review, not a finished legal taxonomy.
"""

from __future__ import annotations

import re

FORMULATION_CATEGORIES: tuple[str, ...] = (
    "classical",
    "proprietary",
    "phytopharmaceutical",
    "ayurveda_aahar",
    "cosmetic",
)

# Checked in this fixed order, first match wins — order matters where terms
# could plausibly overlap (e.g. "patent" alone is too generic to trigger
# "proprietary" on its own; only the P&P-specific phrasing does).
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "ayurveda_aahar",
        re.compile(r"\b(nutraceutical|ayurveda[\s-]*aahar|dietary supplement|functional food|food product)\b", re.I),
    ),
    (
        "cosmetic",
        re.compile(r"\b(cosmetic|soap|face\s*wash|skin\s*care|shampoo|lotion)\b", re.I),
    ),
    (
        "phytopharmaceutical",
        re.compile(r"\b(phytopharmaceutical|botanical drug|standardi[sz]ed (botanical )?extract)\b", re.I),
    ),
    (
        "proprietary",
        re.compile(r"\b(proprietary medicine|patent(?:ed)? or proprietary|p\s*(?:&|and)\s*p\b|proprietary ayurvedic|proprietary formulation)\b", re.I),
    ),
]

# Blueprint-specified tags for classical, proprietary, and Ayurveda-Aahar.
# phytopharmaceutical and cosmetic weren't given explicit tag sets in the
# original request — these are a reasonable extrapolation from how those
# categories are actually regulated (D&C Rules for phytopharmaceuticals,
# no-therapeutic-claim for cosmetics), not a literal spec, and should be
# reviewed the same as the rest of this heuristic.
CATEGORY_STATUTORY_TAGS: dict[str, list[str]] = {
    "classical": ["Patents_Act_Sec3p", "TKDL", "D&C_First_Schedule", "BDA_Exemption"],
    "proprietary": ["Patents_Act_Sec3e", "BDA_Section6_NBA_Form1_Form2", "Clinical_Validation"],
    "phytopharmaceutical": ["D&C_Phytopharmaceutical_Definition", "Clinical_Validation"],
    "ayurveda_aahar": ["FSSAI_Ayurveda_Aahar_Regs_2022", "No_Therapeutic_Claim"],
    "cosmetic": ["D&C_Cosmetic_Rules", "No_Therapeutic_Claim"],
}

CATEGORY_LABELS: dict[str, str] = {
    "classical": "Classical Ayurvedic Medicine",
    "proprietary": "Patent/Proprietary Medicine (P&P)",
    "phytopharmaceutical": "Phytopharmaceutical",
    "ayurveda_aahar": "Ayurveda-Aahar (Nutraceutical)",
    "cosmetic": "Cosmetic",
}


def classify_formulation(query: str) -> str:
    """Keyword-match `query` against each category in priority order,
    defaulting to "classical" — most TK/Section 3(p)-style questions in
    this domain are, in fact, about classical formulations, so an
    unclassifiable question defaulting there is a reasonable prior, not an
    arbitrary fallback."""
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(query):
            return category
    return "classical"

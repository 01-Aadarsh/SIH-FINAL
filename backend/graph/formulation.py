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

# Canonical tag taxonomy. Every name here must match a real tag that
# ingestion/chunker.py::_compile_statutory_tag_rules or
# _compile_heading_tag_rules can actually produce — WIPO_GRATK_Art3_
# Disclosure is the one reserved exception, listed in data/international/
# README.md as not yet firing because no source document exists to tag
# (see idea.md). classical/proprietary/ayurveda_aahar were given
# explicitly; phytopharmaceutical and cosmetic's tag sets are a reasonable
# extrapolation from how those categories are actually regulated (D&C
# Rules for phytopharmaceuticals, no-therapeutic-claim for cosmetics), not
# a literal spec, and should be reviewed the same as the rest of this
# heuristic.
CATEGORY_STATUTORY_TAGS: dict[str, list[str]] = {
    "classical": ["Patents_Act_Sec3p", "Patents_Act_Sec3d", "TKDL", "D&C_First_Schedule", "BDA_Sec7_SBB_Exemption"],
    "proprietary": ["Patents_Act_Sec3e", "BDA_Sec6_NBA_Approval", "Clinical_Validation"],
    "phytopharmaceutical": ["D&C_Rule_158B", "Clinical_Validation"],
    "ayurveda_aahar": ["FSSAI_Ayurveda_Aahar_2022", "No_Therapeutic_Claim"],
    "cosmetic": ["D&C_Cosmetic_Rules", "No_Therapeutic_Claim"],
}

CATEGORY_LABELS: dict[str, str] = {
    "classical": "Classical Ayurvedic Medicine",
    "proprietary": "Patent/Proprietary Medicine (P&P)",
    "phytopharmaceutical": "Phytopharmaceutical",
    "ayurveda_aahar": "Ayurveda-Aahar (Nutraceutical)",
    "cosmetic": "Cosmetic",
}

_CLARIFYING_DESCRIPTIONS: dict[str, str] = {
    "classical": "a classical/traditional Ayurvedic formulation (e.g. from a recognized classical text)",
    "proprietary": "a proprietary (P&P) medicine with a brand name and its own formulation",
    "phytopharmaceutical": "a phytopharmaceutical drug (standardized botanical extract)",
    "ayurveda_aahar": "an Ayurveda-Aahar / nutraceutical food product",
    "cosmetic": "a cosmetic product",
}


def triage_formulation(query: str) -> dict:
    """
    Keyword-match `query` against every category (not just the first hit),
    so genuine ambiguity — two or more categories' keywords both present —
    is detectable, not silently resolved by whichever pattern happens to
    be checked first.

    Zero matches still defaults to "classical" without asking for
    clarification: most TK/Section 3(p)-style questions in this domain
    genuinely are about classical formulations, so an unclassifiable
    question defaulting there is a reasonable prior, not something to
    interrupt the user over. Two or more matches DO ask — that's a real
    signal the question spans categories with materially different
    statutory obligations (e.g. mentioning both "nutraceutical" and
    "proprietary formulation" could mean either), not just coarse
    keyword-matching noise.

    Returns a dict — not a class — since this is exactly what gets merged
    into GraphState (a plain dict-of-keys, per this project's node
    convention; see graph/nodes.py).
    """
    matched = [category for category, pattern in _CATEGORY_PATTERNS if pattern.search(query)]

    if len(matched) >= 2:
        options = " or ".join(_CLARIFYING_DESCRIPTIONS[c] for c in matched)
        question = (
            f"This question touches more than one formulation category — is it "
            f"about {options}? The answer below assumes {CATEGORY_LABELS[matched[0]]} "
            f"unless you clarify."
        )
        return {
            "formulation_category": matched[0],
            "needs_clarification": True,
            "clarifying_questions": [question],
        }

    category = matched[0] if matched else "classical"
    return {
        "formulation_category": category,
        "needs_clarification": False,
        "clarifying_questions": [],
    }


def classify_formulation(query: str) -> str:
    """Category only, no clarification info — thin wrapper over
    triage_formulation() for callers (e.g. tests) that just want the
    category."""
    return triage_formulation(query)["formulation_category"]

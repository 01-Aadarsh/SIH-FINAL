"""
Form & Registry Navigator for IP-SAKTI.

A deterministic (no LLM call, same reasoning as graph/formulation.py's
triage and graph_kg/kg.py's lookups) catalog + keyword matcher that points
a question at the actual official government form it needs next — the
PS's "facilitates access to authoritative sources... move from a question
to the right registry, record or form."

IMPORTANT CORRECTION vs. the original feature request: the NBA form
numbers requested there do not match the real Biological Diversity Rules,
2024 — verified directly against data/BD_Rules_2024.pdf, already indexed
in this project (Rules 13, 15, 16), not from general web knowledge:

  - Real Form 1 = access for RESEARCH / bio-survey / bio-utilisation (Rule
    13(1)) — the request described this content but labeled it "Form 1"
    for COMMERCIAL utilisation, which is actually real Form 2.
  - Real Form 2 = access for COMMERCIAL utilisation (Rule 13(1)).
  - Real Form 3 = prior approval to TRANSFER research results to a third
    party (Rule 15(1)(a)) — the request called this "Form 2".
  - Real Form 4 = transferee's registration to use results for FURTHER
    RESEARCH (Rule 15(1)(b)).
  - Applying for IPR/patents based on Indian biological resources
    (Section 6 of the Act) is real Form 7 (Rule 16(1)(a)), not "Form 3" —
    this is the one that matters most for a patent question, so getting
    the number right here specifically is not a minor detail.
  - Real Form 8 = registration before IPR for Section 7 persons (the
    SBB-exemption / codified-TK community route already tagged
    BDA_Sec7_SBB_Exemption elsewhere in this project) — a materially
    different, additional form worth including, not a renumbering of
    anything in the original request.

Similarly for the IPO side: the request's single "Form 8: early
publication or expedited examination" conflates two distinct real forms —
verified against Sarvam... no, against public IPO practice guides and this
project's own indexed Patent_Office_Manual_Practice_Procedure_2011.pdf
(which cites "Form-9... Rule 24A" for early publication directly):
  - Real Form 9 = request for early publication (Section 11A(2), Rule 24A).
  - Real Form 18A = request for EXPEDITED examination (Rule 24C) — a
    separate form from the plain Form 18 examination request.
  Real Form 8 is unrelated to either — it's "request or claim regarding
  mention of inventor" (Section 28, Rules 66-68).

This module ships the corrected, verified numbers. Same status as every
other heuristic catalog in this codebase (ingestion/chunker.py's tag
rules, graph_kg/build_kg.py's cross-jurisdiction table): a first pass a
domain expert / registered patent agent should review before anyone
relies on it to actually file — submission portals and deadlines in
particular change, and this is not a substitute for professional advice
(see generation/prompts.py's standing disclaimer, which /query already
appends and which this feature does not replace).

Usage:
    python -m compliance.form_navigator "I want to patent my Ayurvedic formulation"
"""

from __future__ import annotations

import sys

NBA_PORTAL = "https://absefiling.nic.in"
IPO_PORTAL = "https://ipindiaonline.gov.in/epatentfiling/goForLogin/doLogin"

# Each entry: the exact fields returned to a caller (form_id, title,
# statutory_mandate, submission_portal, required_attachments, deadline,
# agency, jurisdiction), plus two match-only fields not returned directly:
# `keywords` (free-text intent matching) and `formulation_categories`
# (cross-matched against graph/formulation.py's categories when this
# catalog is consulted automatically from the /query pipeline, not just
# the standalone endpoint).
FORM_CATALOG: list[dict] = [
    {
        "form_id": "NBA_FORM_1",
        "agency": "NBA",
        "jurisdiction": "india",
        "title": "Form 1 - NBA Access for Research / Bio-Survey / Bio-Utilisation",
        "statutory_mandate": "Biological Diversity Act, 2002 Section 3; Biological Diversity Rules, 2024 Rule 13(1)",
        "submission_portal": NBA_PORTAL,
        "required_attachments": [
            "Description of the biological resource and its source location",
            "Purpose and scope of the proposed research or bio-survey",
            "Applicant identity/institutional affiliation proof",
        ],
        "deadline": "Before commencing access to the biological resource",
        "keywords": [
            "bio-survey", "biosurvey", "bio-utilisation", "bio-utilization",
            "bioutilisation", "research access to biological resource",
            "nba form 1", "access for research",
        ],
        "formulation_categories": [],
    },
    {
        "form_id": "NBA_FORM_2",
        "agency": "NBA",
        "jurisdiction": "india",
        "title": "Form 2 - NBA Access for Commercial Utilisation",
        "statutory_mandate": "Biological Diversity Act, 2002 Section 3; Biological Diversity Rules, 2024 Rule 13(1)",
        "submission_portal": NBA_PORTAL,
        "required_attachments": [
            "Description of the biological resource and its source location",
            "Commercial utilisation/business plan summary",
            "Benefit-sharing proposal (Rule 21)",
        ],
        "deadline": "Before commencing commercial access to the biological resource",
        "keywords": [
            "commercial utilization", "commercial utilisation",
            "access for commercial", "biological resource commercial",
            "nba form 2", "sell ayurvedic product commercially",
        ],
        "formulation_categories": ["proprietary"],
    },
    {
        "form_id": "NBA_FORM_3",
        "agency": "NBA",
        "jurisdiction": "india",
        "title": "Form 3 - NBA Prior Approval to Transfer Research Results",
        "statutory_mandate": "Biological Diversity Act, 2002 Section 4; Biological Diversity Rules, 2024 Rule 15(1)(a)",
        "submission_portal": NBA_PORTAL,
        "required_attachments": [
            "Details of the original research access approval",
            "Identity of the transferee and purpose of transfer",
            "Terms of transfer (commercial or otherwise)",
        ],
        "deadline": "Before transferring results of research to another person",
        "keywords": [
            "transfer results of research", "transferring research results",
            "share research results", "nba form 3", "transfer of research",
        ],
        "formulation_categories": [],
    },
    {
        "form_id": "NBA_FORM_4",
        "agency": "NBA",
        "jurisdiction": "india",
        "title": "Form 4 - NBA Transferee Registration for Further Research",
        "statutory_mandate": "Biological Diversity Rules, 2024 Rule 15(1)(b)",
        "submission_portal": NBA_PORTAL,
        "required_attachments": [
            "Proof of receipt of research results from the original applicant",
            "Description of the further research intended",
        ],
        "deadline": "Before using transferred research results for further research",
        "keywords": [
            "transferee registration", "further research registration",
            "nba form 4", "third-party transfer of accessed biological resource",
            "receiving transferred biological resource results",
        ],
        "formulation_categories": [],
    },
    {
        "form_id": "NBA_FORM_7",
        "agency": "NBA",
        "jurisdiction": "india",
        "title": "Form 7 - NBA Prior Approval Before Applying for IPR/Patent",
        "statutory_mandate": "Biological Diversity Act, 2002 Section 6(1); Biological Diversity Rules, 2024 Rule 16(1)(a)",
        "submission_portal": NBA_PORTAL,
        "required_attachments": [
            "Provisional or complete patent specification draft",
            "Proof of source/origin of the biological resource or traditional knowledge",
            "Disclosure of geographical origin (also required internationally — see the WIPO GRATK Treaty's Article 3 disclosure obligation)",
        ],
        "deadline": "Before applying for the patent, in or outside India (not after grant)",
        "keywords": [
            "patent based on biological resource", "ipr based on biological resource",
            "nba approval for patent", "patent traditional knowledge",
            "biological resource patent", "nba prior approval patent",
            "section 6 approval", "patent my ayurvedic formulation",
        ],
        "formulation_categories": ["proprietary"],
        "statutory_tags": ["BDA_Sec6_NBA_Approval", "NBA_Section6"],
    },
    {
        "form_id": "NBA_FORM_8",
        "agency": "NBA",
        "jurisdiction": "india",
        "title": "Form 8 - NBA Registration Before IPR (Section 7 Community/SBB Route)",
        "statutory_mandate": "Biological Diversity Act, 2002 Section 7; Biological Diversity Rules, 2024 Rule 16(2)(a)",
        "submission_portal": NBA_PORTAL,
        "required_attachments": [
            "Evidence of codified traditional knowledge / community source",
            "Undertaking to seek NBA prior approval before commercialisation",
        ],
        "deadline": "Before grant of IPR, in India or abroad",
        "keywords": [
            "sbb exemption", "vaids", "hakims", "codified traditional knowledge",
            "section 7 exemption", "community ip registration",
            "registered ayush practitioner patent",
        ],
        # Deliberately NOT linked to formulation_category "classical" — that
        # category is the *default fallback* for almost any Ayurveda IP
        # question (see graph/formulation.py: "zero matches still defaults
        # to classical"), so tying a specific form to it made this form
        # attach to nearly every answer, including ones with nothing to do
        # with the Section 7 community/SBB route — a real false-positive
        # found by scripts/evaluate_pipeline.py's benchmark run. Keywords
        # and the actual per-chunk statutory_tags below are precise enough
        # signals on their own.
        "formulation_categories": [],
        "statutory_tags": ["BDA_Sec7_SBB_Exemption", "AYUSH_Section7_Exemption"],
    },
    {
        "form_id": "IPO_FORM_1",
        "agency": "IPO",
        "jurisdiction": "india",
        "title": "Form 1 - Application for Grant of Patent",
        "statutory_mandate": "Patents Act, 1970; Patents Rules, 2003 (Schedule I, Form 1)",
        "submission_portal": IPO_PORTAL,
        "required_attachments": [
            "Applicant and inventor details",
            "Priority document details (if claiming convention priority)",
            "Form 2 (specification) filed alongside or within the prescribed period",
        ],
        "deadline": "At the time of filing the patent application",
        "keywords": [
            "patent application", "grant of patent", "file a patent",
            "new patent application", "how to patent",
        ],
        "formulation_categories": ["proprietary"],
    },
    {
        "form_id": "IPO_FORM_2",
        "agency": "IPO",
        "jurisdiction": "india",
        "title": "Form 2 - Provisional / Complete Specification",
        "statutory_mandate": "Patents Act, 1970 Section 9; Patents Rules, 2003 (Schedule I, Form 2)",
        "submission_portal": IPO_PORTAL,
        "required_attachments": [
            "Description of the invention",
            "Claims (complete specification only)",
            "Abstract and drawings, if applicable",
        ],
        "deadline": "Complete specification within 12 months of filing a provisional specification",
        "keywords": [
            "specification", "provisional specification", "complete specification",
            "patent claims", "patent description",
        ],
        "formulation_categories": ["proprietary"],
    },
    {
        "form_id": "IPO_FORM_5",
        "agency": "IPO",
        "jurisdiction": "india",
        "title": "Form 5 - Declaration as to Inventorship",
        "statutory_mandate": "Patents Rules, 2003 (Schedule I, Form 5)",
        "submission_portal": IPO_PORTAL,
        "required_attachments": ["Inventor name(s) and details"],
        "deadline": "Ordinarily filed with the complete specification",
        "keywords": [
            "declaration of inventorship", "inventor declaration",
            "who is the inventor",
        ],
        "formulation_categories": ["proprietary"],
    },
    {
        "form_id": "IPO_FORM_9",
        "agency": "IPO",
        "jurisdiction": "india",
        "title": "Form 9 - Request for Early Publication",
        "statutory_mandate": "Patents Act, 1970 Section 11A(2); Patents Rules, 2003 Rule 24A",
        "submission_portal": IPO_PORTAL,
        "required_attachments": ["Prescribed fee (concessional for natural persons/startups)"],
        "deadline": "Any time before the ordinary 18-month publication date",
        "keywords": [
            "early publication", "publish patent application early",
            "section 11a",
        ],
        "formulation_categories": ["proprietary"],
    },
    {
        "form_id": "IPO_FORM_18A",
        "agency": "IPO",
        "jurisdiction": "india",
        "title": "Form 18A - Request for Expedited Examination",
        "statutory_mandate": "Patents Rules, 2003 Rule 24C",
        "submission_portal": IPO_PORTAL,
        "required_attachments": [
            "Proof of eligibility category (e.g. recognized startup — verify current Rule 24C eligibility categories directly with IPO, as these are periodically amended)",
        ],
        "deadline": "Any time after filing the request for examination (Form 18), before the First Examination Report issues",
        "keywords": [
            "expedited examination", "fast track examination", "fast-track patent",
            "rule 24c", "startup patent examination",
        ],
        "formulation_categories": ["proprietary"],
    },
]


def _to_form_card(form: dict) -> dict:
    """Only the fields the feature spec actually asks a caller to see —
    `keywords`, `formulation_categories`, and `statutory_tags` are match
    machinery, not part of the documented response shape."""
    return {
        "form_id": form["form_id"],
        "agency": form["agency"],
        "jurisdiction": form["jurisdiction"],
        "title": form["title"],
        "statutory_mandate": form["statutory_mandate"],
        "submission_portal": form["submission_portal"],
        "required_attachments": form["required_attachments"],
        "deadline": form["deadline"],
    }


def match_forms(
    intent: str,
    jurisdiction: str = "india",
    formulation_category: str | None = None,
    statutory_tags: list[str] | None = None,
) -> list[dict]:
    """
    Match `intent` (free text — a query, or explicit keywords) against the
    form catalog. Every catalog entry is a domestic Indian registry, so any
    `jurisdiction` other than "india" returns [] — abstaining rather than
    guessing at an international-registry equivalent this catalog doesn't
    actually cover, same rule the rest of this project applies to
    unindexed jurisdictions.

    `formulation_category` and `statutory_tags` are optional secondary
    signals (used when this is called automatically from the /query
    pipeline, where both already exist from triage) — a keyword hit is
    sufficient on its own; these only ever ADD matches, never suppress a
    keyword hit.
    """
    if jurisdiction != "india":
        return []

    intent_lower = (intent or "").lower()
    tags = set(statutory_tags or [])
    matched_ids: set[str] = set()
    results: list[dict] = []

    for form in FORM_CATALOG:
        keyword_hit = any(kw in intent_lower for kw in form["keywords"])
        category_hit = bool(
            formulation_category and formulation_category in form.get("formulation_categories", [])
        )
        tag_hit = bool(tags & set(form.get("statutory_tags", [])))

        if (keyword_hit or category_hit or tag_hit) and form["form_id"] not in matched_ids:
            matched_ids.add(form["form_id"])
            results.append(_to_form_card(form))

    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    intent_arg = " ".join(sys.argv[1:]) or ""
    if not intent_arg:
        print('Usage: python -m compliance.form_navigator "<intent text>"')
        sys.exit(1)
    for card in match_forms(intent_arg):
        print(card)

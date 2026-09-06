"""
Compliance checkpoint flags for IP-SAKTI.

Deterministic (no LLM call — same reasoning as graph/formulation.py's
triage, graph_kg/kg.py's lookups, and compliance/form_navigator.py's
keyword matching): surfaces a short, generic pointer when an answer's
*actually cited* chunks carry a statutory tag that marks a well-known
compliance checkpoint (an NBA approval gate, the Section 3(p) patent bar,
a therapeutic-claim restriction, and so on) — something a non-expert
asking this system precisely because they aren't a patent agent might
skim past in a wall of statute text.

What this deliberately does NOT do: assert a fee, percentage, timeline,
case outcome, or any other specific fact not already sitting in the cited
chunk. An earlier draft of this feature hardcoded exactly that (invented
NBA processing fees, EU registration costs, patent-litigation success
rates) — none of it traceable to an indexed, page-numbered source. That
draft was discarded, not fixed, because inventing figures and presenting
them as verified regulatory guidance is precisely the failure mode
idea.md's abstention/citation design exists to prevent. Every note here is
generic enough to be true regardless of the specific numbers in play, and
every flag names the tag whose real citation (already in the response's
own `citations` list) backs it — this is a highlighting layer over
information already retrieved and cited, not a second source of facts.

Usage:
    python -m generation.compliance_flags Patents_Act_Sec3p BDA_Sec6_NBA_Approval
"""

from __future__ import annotations

import sys

# One short, generic, non-jurisdiction-specific sentence per tag — no fee,
# date, or percentage ever belongs in this table. `tag` must be one chunker
# actually produces (ingestion/chunker.py::_compile_statutory_tag_rules /
# _compile_heading_tag_rules) — a flag whose tag isn't real would silently
# never fire, so backend/tests covers that every key here exists in that
# vocabulary.
CHECKPOINT_NOTES: dict[str, str] = {
    "Patents_Act_Sec3p": (
        "This touches the Patents Act's Section 3(p) traditional-knowledge "
        "exclusion — worth reading the exact clause in the citation above "
        "before assuming a specific formulation is or isn't covered by it."
    ),
    "Patents_Act_Sec3d": (
        "This touches Section 3(d) (patentability of new forms of a known "
        "substance) — a common additional bar alongside 3(p) for "
        "Ayurveda-derived inventions; see the cited clause above."
    ),
    "Patents_Act_Sec3e": (
        "This touches Section 3(e) (mere admixture / aggregation of known "
        "properties) — relevant to any multi-ingredient formulation claim; "
        "see the cited clause above."
    ),
    "BDA_Sec6_NBA_Approval": (
        "This touches the Biological Diversity Act's Section 6 requirement "
        "— National Biodiversity Authority approval before an IPR "
        "application based on an Indian biological resource. See the cited "
        "passage above for exactly when it applies, and 'related "
        "provisions' below for the international ABS counterpart if this "
        "is headed outside India."
    ),
    "BDA_Sec7_SBB_Exemption": (
        "This touches the Biological Diversity Act's Section 7 route "
        "(registered AYUSH practitioners / codified traditional knowledge) "
        "— a different pathway from the Section 6 NBA-approval route above; "
        "see the cited passage for which one actually applies here."
    ),
    "TKDL": (
        "This touches the Traditional Knowledge Digital Library — used as "
        "prior-art evidence against patent applications, including outside "
        "India. See the cited passage above for how."
    ),
    "D&C_Rule_158B": (
        "This touches the Drugs & Cosmetics Rules' phytopharmaceutical "
        "pathway (Rule 158B) — a distinct regulatory route from a classical "
        "Ayurvedic-formulation approval; see the cited passage above."
    ),
    "No_Therapeutic_Claim": (
        "This touches a restriction on therapeutic claims — see the cited "
        "passage above for the exact wording of what can and can't be "
        "claimed."
    ),
    "Clinical_Validation": (
        "This touches a clinical-trial/validation requirement — see the "
        "cited passage above for what evidence it actually calls for."
    ),
    "Mandatory_Patent_Disclosure": (
        "This touches the WIPO GRATK Treaty's mandatory disclosure-of-origin "
        "obligation for international patent applications based on genetic "
        "resources or associated traditional knowledge — see the cited "
        "passage above."
    ),
    "ABS_Formula": (
        "This touches the Biological Diversity Rules' benefit-sharing "
        "earmark — see the cited passage above for the actual range "
        "specified, rather than assuming a fixed figure."
    ),
}


def flag_compliance_checkpoints(statutory_tags: list[str]) -> list[dict]:
    """
    `statutory_tags` should be the tags actually carried by the chunks that
    grounded this specific answer (the same source api/main.py already
    computes for actionable_forms — `chunk.statutory_tags` on the reranked
    chunks), not a coarser per-category tag list. Returns [] for any tag
    with no note defined, rather than guessing at a generic message for an
    unrecognized tag.
    """
    seen: set[str] = set()
    flags: list[dict] = []
    for tag in statutory_tags:
        if tag in seen:
            continue
        note = CHECKPOINT_NOTES.get(tag)
        if note is None:
            continue
        seen.add(tag)
        flags.append({"tag": tag, "note": note})
    return flags


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tags_arg = sys.argv[1:]
    if not tags_arg:
        print("Usage: python -m generation.compliance_flags <tag> [<tag> ...]")
        sys.exit(1)
    for flag in flag_compliance_checkpoints(tags_arg):
        print(f"[{flag['tag']}] {flag['note']}")

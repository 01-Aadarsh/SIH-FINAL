"""
Tests for generation/compliance_flags.py — see its module docstring for
why every note is deliberately generic (no fee/timeline/percentage), and
why every tag key must exist in ingestion/chunker.py's real tag
vocabulary rather than being an invented one this feature made up.

Usage:
    python -m pytest tests/test_compliance_flags.py -v
"""

from __future__ import annotations

import re

from generation.compliance_flags import CHECKPOINT_NOTES, flag_compliance_checkpoints
from ingestion.chunker import _compile_heading_tag_rules, _compile_statutory_tag_rules

# A currency symbol or percent sign anywhere in a note is a strong signal
# someone hardcoded a specific fee/rate this module's own docstring says
# never to hardcode — catches a regression toward the fabricated-figures
# failure mode this feature was rebuilt to avoid. Deliberately NOT a bare
# \d+ check: "Section 3(p)"/"Section 6" section-number references are
# legitimate and unavoidable (they're the tag vocabulary itself), not
# invented figures.
_SUSPICIOUS_FIGURE_PATTERN = re.compile(r"[₹$€%]")


def test_empty_input_returns_empty_not_error():
    assert flag_compliance_checkpoints([]) == []


def test_unknown_tag_returns_empty_not_error():
    assert flag_compliance_checkpoints(["Some_Made_Up_Tag"]) == []


def test_known_tag_returns_its_note():
    result = flag_compliance_checkpoints(["Patents_Act_Sec3p"])
    assert result == [
        {"tag": "Patents_Act_Sec3p", "note": CHECKPOINT_NOTES["Patents_Act_Sec3p"]}
    ]


def test_duplicate_tags_are_deduplicated():
    result = flag_compliance_checkpoints(["TKDL", "TKDL", "TKDL"])
    assert len(result) == 1


def test_mixed_known_and_unknown_tags():
    result = flag_compliance_checkpoints(["Patents_Act_Sec3p", "Not_A_Real_Tag", "TKDL"])
    tags_returned = {f["tag"] for f in result}
    assert tags_returned == {"Patents_Act_Sec3p", "TKDL"}


def test_every_checkpoint_tag_exists_in_the_real_chunker_vocabulary():
    """A flag whose tag chunker.py never actually produces would silently
    never fire — same precondition graph_kg/build_kg.py enforces on its own
    CROSS_JURISDICTION_PAIRS table."""
    real_tags = {tag for _, _, tag in _compile_statutory_tag_rules()}
    real_tags |= {tag for _, _, tag in _compile_heading_tag_rules()}
    unknown = set(CHECKPOINT_NOTES) - real_tags
    assert not unknown, (
        f"CHECKPOINT_NOTES references tag(s) chunker.py doesn't actually "
        f"produce: {sorted(unknown)}"
    )


def test_no_note_contains_a_fabricated_specific_figure():
    """See this module's docstring: a fee, percentage, or count is exactly
    the kind of invented specific this feature exists to never assert."""
    offenders = {
        tag: note
        for tag, note in CHECKPOINT_NOTES.items()
        if _SUSPICIOUS_FIGURE_PATTERN.search(note)
    }
    assert not offenders, f"Note(s) contain a suspicious specific figure: {offenders}"

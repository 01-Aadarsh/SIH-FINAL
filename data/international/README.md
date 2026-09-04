# International-jurisdiction documents

Empty placeholder. PDFs dropped directly in this folder are ingested with
`jurisdiction = "international"` — the folder location is what tags them,
not filename guessing (see `backend/ingestion/loader.py::load_directory`).

A query sent with `jurisdiction: "international"` filters retrieval to only
chunks tagged this way. Until real documents land here, that filter
correctly returns nothing, and the pipeline's normal abstention guardrail
handles it — "I could not find this in my sources" — rather than crashing
or silently answering from the India-only corpus in `data/`.

Candidates for this folder, per the PS's international scope (WIPO, CBD/
Nagoya Protocol, PCT — treaty texts and implementing guidance, not India's
own Acts that happen to implement them; e.g. the Biological Diversity Act
is India's domestic ABS law and stays in `data/`, not here):

- **WIPO Treaty on Intellectual Property, Genetic Resources and Associated
  Traditional Knowledge (the "GRATK Treaty", adopted May 2024)** — mandatory
  disclosure of origin obligations for patent applications relying on
  genetic resources or associated TK. The specific reason
  `graph/formulation.py`'s classical-formulation statutory tags include
  `TKDL` and `Patents_Act_Sec3p`: once this treaty is indexed, those tags
  are exactly what should co-occur with its disclosure-obligation clauses.
- **Nagoya Protocol on Access and Benefit-Sharing** (full text) — the
  international ABS counterpart to the domestic BD Act's Section 6/NBA
  process already tagged `BDA_Sec6_NBA_Approval` (see
  `ingestion/chunker.py::tag_statutory_metadata`).
- **Budapest Treaty** (international recognition of microorganism deposit
  for patent procedure) — relevant to any classical/proprietary Ayurvedic
  formulation claim involving a deposited microbial strain.
- **Patent Cooperation Treaty (PCT)** — international filing route.
- WIPO Intergovernmental Committee (IGC) materials on traditional knowledge / genetic resources more broadly.

None of these are indexed yet — this folder is empty as of this writing.
Reserved (not yet applied to any chunk) statutory tag names for when they
are: `WIPO_GRATK_Art3_Disclosure` (Article 3's mandatory origin-disclosure
obligation specifically — Articles 4-7 covering exceptions, information
systems, and sanctions/remedies would need their own tags once the treaty
text is actually here and its real structure is known, not guessed),
`Nagoya_ABS_Clearing_House`, `Budapest_Treaty_Deposit`. Add them to
`ingestion/chunker.py::_STATUTORY_TAG_RULES` alongside real source-file/
keyword rules once the actual PDF is here — a reserved name with no rule
pointed at real text would never fire, so don't add the tag name without
also adding the rule.

After adding PDFs here, re-run ingestion (`python -m ingestion.indexer`,
or `POST /ingest`) to index them.

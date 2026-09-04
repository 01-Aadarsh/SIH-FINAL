# International-jurisdiction documents

PDFs dropped directly in this folder are ingested with `jurisdiction =
"international"` — the folder location is what tags them, not filename
guessing (see `backend/ingestion/loader.py::load_directory`).

A query sent with `jurisdiction: "international"` filters retrieval to only
chunks tagged this way. Documents not yet indexed here still correctly
abstain rather than crash or silently answer from the India-only corpus in
`data/` — that's the intended behavior for anything genuinely missing, not
a gap to route around.

**Indexed:**

- **`WIPO_GRATK_Treaty_2024.pdf` — WIPO Treaty on Intellectual Property,
  Genetic Resources and Associated Traditional Knowledge** (adopted at
  Geneva, May 24, 2024). Downloaded directly from WIPO's own treaty-text
  archive (wipolex-res.wipo.int/edocs/lexdocs/treaties/en/gratk/
  trt_gratk_001en.pdf, linked from
  https://www.wipo.int/wipolex/en/treaties/textdetails/19849) and verified
  page-by-page against the extracted PDF text before indexing — not
  reconstructed from memory. Tagged `WIPO_GRATK_2024` (whole document),
  `Mandatory_Patent_Disclosure` (Article 3's disclosure obligation),
  `Genetic_Resources`, `Traditional_Knowledge` — see
  `ingestion/chunker.py::_compile_statutory_tag_rules`.

Still-missing candidates, per the PS's international scope (treaty texts
and implementing guidance, not India's own Acts that happen to implement
them — e.g. the Biological Diversity Act is India's domestic ABS law and
stays in `data/`, not here):

- **Nagoya Protocol on Access and Benefit-Sharing** (full text) — the
  international ABS counterpart to the domestic BD Act's Section 6/NBA
  process already tagged `BDA_Sec6_NBA_Approval` (see
  `ingestion/chunker.py::tag_statutory_metadata`).
- **Budapest Treaty** (international recognition of microorganism deposit
  for patent procedure) — relevant to any classical/proprietary Ayurvedic
  formulation claim involving a deposited microbial strain.
- **Patent Cooperation Treaty (PCT)** — international filing route.
- WIPO Intergovernmental Committee (IGC) materials on traditional knowledge / genetic resources more broadly.

Reserved (not yet applied to any chunk) statutory tag names for when a real
source document lands for these: `Nagoya_ABS_Clearing_House`,
`Budapest_Treaty_Deposit`. Add them to
`ingestion/chunker.py::_compile_statutory_tag_rules` alongside real
source-file/keyword rules once the actual PDF is here — a reserved name
with no rule pointed at real text would never fire, so don't add the tag
name without also adding the rule. This is exactly the process
`WIPO_GRATK_2024` above went through: reserved name first, real rule added
only once the real PDF existed to point it at.

After adding PDFs here, re-run ingestion (`python -m ingestion.indexer`,
or `POST /ingest`) to index them.

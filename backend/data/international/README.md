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

- **`Nagoya_Protocol_2010.pdf` — Nagoya Protocol on Access to Genetic
  Resources and the Fair and Equitable Sharing of Benefits Arising from
  their Utilization** (2010). Downloaded from the CBD Secretariat's own
  site (cbd.int/abs/doc/protocol/nagoya-protocol-en.pdf). The
  international ABS counterpart to the domestic BD Act's Section 6/NBA
  process (tagged `BDA_Sec6_NBA_Approval` on the India side). Tagged
  `Nagoya_Protocol_2010` (whole document), `Nagoya_ABS_Clearing_House`
  (the ABS Clearing-House mechanism).

- **`Budapest_Treaty_1977.pdf` — Budapest Treaty on the International
  Recognition of the Deposit of Microorganisms for the Purposes of Patent
  Procedure** (1977, amended 1980) — the actual treaty article text
  (Articles 1 through the final provisions), not a summary. WIPO no longer
  serves this specific treaty as a standalone PDF from wipolex (the old
  wipolex-res.wipo.int PDF mirror 404s/redirects for it); the article text
  was extracted from WIPO Lex's own server-rendered page
  (wipo.int/wipolex/en/text/283784), which does contain the full text in
  its HTML — verified by confirming Article 1 through Article 17 and the
  final clauses are all present — then rendered to PDF with a plain
  HTML-to-PDF script (`git log` for `_build_budapest_pdf.py`, not kept in
  the repo since it was a one-off corpus-prep tool, not app code). No LLM
  was used to produce or reconstruct any of the text; only tag-stripping,
  whitespace normalization, and ASCII punctuation normalization (en-dash
  → hyphen, curly quotes → straight quotes) were applied. Tagged
  `Budapest_Treaty_1977` (whole document), `Budapest_Treaty_Deposit`
  (the deposit/depositary-authority provisions) — relevant to any
  classical/proprietary Ayurvedic formulation claim involving a deposited
  microbial strain.

- **`Budapest_Treaty_WIPO_Secretariat_Note.pdf` — WO/INF/12, WIPO
  Secretariat's official summary and status note on the Budapest Treaty**
  (parties list, background, main advantages, depositary-institution
  list). A distinct document from the treaty text above — kept separately
  rather than merged in, so a citation always points at which one it
  actually came from (same reasoning as `BD_Amendment_Act_2023` being
  kept distinct from `Biological_Diversity_Act_2002.pdf` — see that
  rule's comment in `chunker.py`). Tagged
  `Budapest_Treaty_Secretariat_Note` (whole document); also matches the
  shared `Budapest_Treaty_Deposit` keyword rule.

- **`PCT_1970.pdf` — Patent Cooperation Treaty** (done at Washington,
  June 19, 1970; amended 1979; modified 1984 and 2001) — the real, full
  53-article treaty text, downloaded from
  wipo.int/documents/d/pct-system/docs-en-texts-pct.pdf and verified by
  content (not just filename) to be the actual treaty articles, not a
  status/member-list document. **Not yet tagged** with a dedicated
  statutory tag — only generically indexed and retrievable. Add a
  `PCT_*` rule set to `chunker.py::_compile_statutory_tag_rules` if a
  specific PCT provision needs its own citation tag later (e.g. Article 8
  priority claims, Chapter II international preliminary examination).

- **`WIPO_IGC_Mandate_2026_2027.pdf` — WIPO General Assembly decision
  renewing the Intergovernmental Committee on Intellectual Property and
  Genetic Resources, Traditional Knowledge and Folklore (IGC) mandate**
  for the 2026/2027 biennium (Sixty-Sixth Series of Meetings, July 2025).
  Real WIPO Assembly decision document, not a fabricated summary. **Not
  yet tagged** with a dedicated statutory tag — the IGC's broader
  negotiating-history materials (WIPO/GRTKF/IC/51/4, WIPO/GRTKF/IC/51/5,
  etc., referenced in this decision but not separately indexed) remain a
  possible future addition if deeper IGC coverage is needed.

**Still missing**, per the PS's international scope: none of the
originally-identified candidates remain unaddressed. If broader coverage
is wanted later, natural next candidates are the IGC's own draft-articles
working documents (referenced above) and herbal-product market-access
guidance from specific export-market regulators (the PS's "herbal-product
market-access regimes of key export markets," which is jurisdiction-
specific and was intentionally left open rather than guessed at).

After adding PDFs here, re-run ingestion (`python -m ingestion.indexer`,
or `POST /ingest`) to index them.

# Document corpus reference

IP-SAKTI Sahayak answers questions **only** from indexed official
documents (see `idea.md`'s abstention-guardrail rule). This file lists
exactly what's indexed, queried directly from the live database on
2026-09-07 — not a hand-maintained guess that can drift from reality. To
reproduce these numbers yourself:

```sql
SELECT jurisdiction, source_file, COUNT(*) FROM chunks
GROUP BY jurisdiction, source_file ORDER BY jurisdiction, source_file;
```

**If you add a new PDF**, this file goes stale until you re-run the query
above — don't hand-edit counts.

## Totals

| Jurisdiction | Documents | Chunks |
|---|---|---|
| India (domestic) | 24 | 4,624 |
| International | 6 | 180 |
| **Total** | **30** | **4,804** |

1,299 of those 4,804 chunks (27%) carry at least one statutory tag from
`ingestion/chunker.py::tag_statutory_metadata` — a best-effort
keyword/heading classifier, not exhaustive coverage of every chunk; an
untagged chunk is still fully retrievable and citable, it just isn't
pre-labeled with a tag like `Patents_Act_Sec3p`.

## India (domestic)

| Document | Chunks |
|---|---|
| Patents_Act_1970.pdf | 511 |
| Trade_Marks_Act_1999.pdf | 474 |
| Drugs_and_Cosmetics_Act_and_Rules.pdf | 1,299 |
| NDCT_Rules_2019.pdf | 352 |
| Copyright_Act_1957.pdf | 267 |
| GI_Rules_2002.pdf | 243 |
| BD_Rules_2024.pdf | 196 |
| Nutraceuticals_Regulations.pdf | 188 |
| Patent_Office_Manual_Practice_Procedure_2011.pdf | 185 |
| Biological_Diversity_Act_2002.pdf | 182 |
| Jan_Vishwas_Amendment_Act_2023.pdf | 181 |
| PPVFR_Act_2001.pdf | 91 |
| GI_Act_1999.pdf | 71 |
| Documenting_Traditional_Knowledge_EACPM.pdf | 64 |
| WIPO_TK_Background_Brief.pdf | 64 |
| National_IPR_Policy_2016.pdf | 56 |
| Drugs_and_Magic_Remedies_Objectionable_Advertisements_Act_1954.pdf | 48 |
| Gazette_Notification_Ayurveda_Aahara_09_05_2022.pdf | 37 |
| Patents_Amendment_Rules_2024.pdf | 31 |
| BD_Amendment_Act_2023.pdf | 30 |
| IPIndia_Patent_Guidelines_TK_Biological_Material.pdf | 18 |
| CTRI_Registration_Guidelines.pdf | 16 |
| GI_Rules_2025_Amendment.pdf | 15 |
| PIB_TKDL_Factsheet_2022.pdf | 5 |

Representative questions this set actually answers well (see
`docs/DEMO_QUERIES.md` for live-verified examples): Section 3(p) /
traditional-knowledge patent exclusions, NBA prior-approval under the
Biological Diversity Act, Geographical Indication registration and
refusal grounds, trademark definitions, Ayurvedic drug and nutraceutical
regulation under the Drugs & Cosmetics Act.

## International

| Document | Chunks |
|---|---|
| PCT_1970.pdf | 87 |
| Nagoya_Protocol_2010.pdf | 38 |
| Budapest_Treaty_1977.pdf | 22 |
| WIPO_GRATK_Treaty_2024.pdf | 17 |
| Budapest_Treaty_WIPO_Secretariat_Note.pdf | 13 |
| WIPO_IGC_Mandate_2026_2027.pdf | 3 |

See `data/international/README.md` for exactly why each of these six was
chosen and what's deliberately *not* here (e.g. the IGC's deeper
negotiating-history documents — a `jurisdiction: "international"` question
about those correctly abstains rather than guessing).

**Not indexed, despite being commonly asked about**: EU Directive
2004/24/EC (Traditional Herbal Registration), US FDA Botanical Drug
Guidance, and any country-specific patent office beyond India/PCT/WIPO. A
question naming these should abstain — that's the intended behavior of
the guardrail described in `idea.md`, not a gap silently papered over with
a plausible-sounding but unverifiable answer.

## How this is used

1. **Retrieval**: BM25 is genuinely partitioned per `jurisdiction` (one
   index per jurisdiction, not one shared index filtered afterward — see
   `ingestion/indexer.py::build_bm25`), and dense retrieval filters in SQL.
2. **Citations**: every claim in an answer traces back to one of the rows
   above by `source_file` + `page_number` — click "View source PDF" on any
   citation to see the exact page.
3. **Abstention**: a question about a real regulatory topic this corpus
   genuinely doesn't cover (an EU directive, a US statute, a document not
   in the tables above) is expected to trigger `flags.abstained = true`,
   not a hallucinated answer built from general training-data knowledge.
   See `docs/DEMO_QUERIES.md`'s query #7 for a live-verified example of
   this working as designed.

## Adding a new document

```bash
# From backend/, venv active
cp new_regulation.pdf ../data/            # or ../data/international/
python -m ingestion.indexer --reset
python -m graph_kg.build_kg               # re-run after any tagging change
```

`indexer.py` fails loudly (not silently) if any chunk is missing citation
metadata — that's deliberate, since every downstream citation depends on
it. Re-run the `SELECT ... GROUP BY` query at the top of this file
afterward and update the tables above.

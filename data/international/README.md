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

- WIPO Intergovernmental Committee (IGC) materials on traditional knowledge / genetic resources
- Nagoya Protocol on Access and Benefit-Sharing (full text)
- Patent Cooperation Treaty (PCT) — international filing route
- WIPO-administered treaties relevant to GI/trademark protection abroad

After adding PDFs here, re-run ingestion (`python -m ingestion.indexer`,
or `POST /ingest`) to index them.

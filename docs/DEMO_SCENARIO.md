# A worked demo scenario — real output, not a script

Unlike a scripted walkthrough, everything under "Actual output" below is
copied verbatim from a real `/query` response, run against the live
backend on 2026-09-07 (raw response saved in this session; re-run it
yourself to reproduce). Nothing here is invented — if a number, form ID,
or citation looks suspiciously convenient, that's because it's exactly
what the system actually returned, not what a script says it should
return.

**Why this matters**: an earlier draft of this document scripted a
multi-jurisdiction Hindi conversation including specific EU compliance
citations, fees, and page numbers. None of that was tested, and some of
it referenced documents this corpus doesn't actually have indexed (an EU
herbal directive — see `docs/DOCUMENT_CORPUS.md` for what's really there).
That draft was discarded rather than fixed, because presenting invented
figures as verified system output is exactly the failure mode this
project's abstention/citation design exists to prevent.

## Scenario: "Can I patent an Ashwagandha extract?"

### Question asked

> Can I patent an extract of Ashwagandha, and do I need any biodiversity
> clearance first?

**Jurisdiction:** India. **Formulation category** (auto-triaged): classical.

### Actual output

**Abstained:** false · **Confidence:** 79.8% · **Retried:** true — the
bounded single retry fired on this query before answering (see
`graph/nodes.py::rerank_node`), visible proof the retry mechanism is real,
not just documented.

**Answer:**

> Under Indian law, an Ashwagandha extract that is derived from a
> biological resource occurring in India is treated as a "biological
> material" for patent purposes.
>
> **Biodiversity clearance**
> Any person who intends to obtain a patent (or any other form of IP) on
> such material must first apply to the National Biodiversity Authority
> (NBA) in the form and on payment of fees prescribed under the
> Biological Diversity Act, 2002 (see Chapter V, Section 19). The NBA must
> decide the application within 90 days of receipt. The patent applicant
> must file a declaration in Form-1 (Patent Rules 2003) stating that the
> invention uses Indian biological material and that the required NBA
> permission will be submitted before the patent is granted.
>
> **Consequences of non-compliance**
> Section 55(1) of the Biological Diversity Act provides for imprisonment
> of up to 5 years, a fine of up to ₹10 lakh (or higher if damage exceeds
> that amount), or both. A patent that contravenes the Act can be refused
> under Section 15 of the Patents Act, 1970, or opposed in the pre-grant
> stage under clauses (d), (f) and (k) of Section 25(1).
>
> **Role of TKDL**
> If the Ashwagandha extract is based on knowledge recorded in the
> Traditional Knowledge Digital Library, CSIR-TKDL may file third-party
> observations or pre-grant oppositions, and TKDL prior-art evidence can
> lead to withdrawal, amendment, or cancellation.

*(Every fee, section number, and timeline above is quoted from the actual
retrieved chunks below — not separately asserted.)*

**Citations** (5, in reranked order):

| # | Source | Page | Section |
|---|---|---|---|
| 1 | IPIndia_Patent_Guidelines_TK_Biological_Material.pdf | 3 | Knowledge and Biological Material |
| 2 | PIB_TKDL_Factsheet_2022.pdf | 3 | About TKDL |
| 3 | IPIndia_Patent_Guidelines_TK_Biological_Material.pdf | 2 | Knowledge and Biological Material |
| 4 | Biological_Diversity_Act_2002.pdf | 12 | Section 19, clause (1) |
| 5 | Biological_Diversity_Act_2002.pdf | 6 | Section 2, clause (p) |

**Actionable forms** (`compliance/form_navigator.py`, deterministic
keyword match, no LLM call):

- **NBA Form 7** — prior approval before applying for a patent/IPR
  (Biological Diversity Act §6(1); BD Rules 2024 Rule 16(1)(a)). Portal:
  `absefiling.nic.in`. Due before filing, in or outside India.
- **NBA Form 8** — registration before IPR via the community/SBB route
  (BD Act §7; BD Rules 2024 Rule 16(2)(a)).

**Related provisions** (knowledge graph, `graph_kg/`) — surfaced even
though this was an India-jurisdiction question, pointing at real,
separately-citable international documents:

- `Nagoya_ABS_Clearing_House` → Nagoya Protocol 2010, p.11
- `Traditional_Knowledge` → WIPO GRATK Treaty 2024, p.1, Article 1
- `Genetic_Resources` → WIPO GRATK Treaty 2024, p.1, Article 1

### Now switch jurisdiction to International

Toggling the jurisdiction switch and asking the natural international
follow-up —

> What are the mandatory disclosure of origin obligations for patent
> applicants under the WIPO GRATK Treaty 2024?

— is `docs/DEMO_QUERIES.md`'s query #5, verified live separately (95.4%
confidence, citing WIPO_GRATK_Treaty_2024.pdf Article 3). Running it right
after the India query above makes the point concretely: the `related
provisions` the India answer already pointed at (WIPO GRATK Treaty,
Nagoya Protocol) are real, independently retrievable, independently
citable documents in this same corpus — not a dead-end label.

## What this proves, without needing to claim more than it showed

1. **Citations are real and specific** — page numbers and section clauses
   a person can open and check (`View source PDF` on each one).
2. **The retry mechanism isn't just documented, it fires** — `retried:
   true` on this exact query.
3. **Actionable forms are deterministic, not generated** — same NBA form
   numbers and portal URL every time this question is asked, because
   they're keyword-matched from a fixed catalog, not written by the LLM.
4. **The knowledge graph makes real cross-jurisdiction connections** — an
   India-only answer still surfaces the Nagoya Protocol and WIPO GRATK
   Treaty as genuinely related, separately-verifiable documents.
5. **Confidence (79.8%) reflects retrieval quality, not a claim of
   certainty** — it's the cross-encoder's score, shown alongside the
   answer so a reader can judge how strongly grounded it is themselves.

## Reproducing this

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Can I patent an extract of Ashwagandha, and do I need any biodiversity clearance first?"}'
```

Re-run before a real demo if the corpus or prompts have changed since
2026-09-07 — this document makes no claim beyond what it actually showed
that day.

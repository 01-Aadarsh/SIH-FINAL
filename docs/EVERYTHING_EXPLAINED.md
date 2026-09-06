# Everything Explained — IP-SAKTI Sahayak, From Zero

This document assumes **you know nothing about this project, and maybe
nothing about coding at all.** By the end, you should understand what we
built, why it matters, how every piece works, and how to run it yourself
on your own laptop. No jargon appears without being explained the moment
it shows up.

---

# 1. The Real-World Problem (Explain Like I'm 10)

### What is Ayurveda?

Ayurveda is India's traditional system of medicine — thousands of years
old, built around herbs, minerals, and combinations of them (called
**formulations**) to treat illness. Things like **Triphala** (a mix of
three fruits used for digestion), **turmeric paste** for wounds, or
**Ashwagandha** root powder for stress are classical Ayurvedic
formulations — recipes that have been written down and used for
generations, often in ancient texts like the *Charaka Samhita*.

### The Patent Trap: what is "Section 3(p)"?

A **patent** is a government-granted monopoly: if you invent something
genuinely new, the government lets you be the only one who can sell it
for a period of time (usually 20 years), in exchange for you publicly
describing how it works.

Here's the trap: **you cannot patent something that already exists and
is already known.** Indian patent law says this explicitly, in
**Section 3(p) of the Patents Act, 1970** — it says an invention that is,
"in effect, traditional knowledge" is *not* a real invention, and
therefore cannot be patented. This makes sense: if Triphala churna has
been used by millions of people for hundreds of years, nobody should be
able to walk in tomorrow and claim they "invented" it and lock everyone
else out.

But this creates a real, everyday problem for **Ayurvedic startups**:
someone building a genuinely new product — say, a novel *extraction
method* for a classical herb, or a new combination with real clinical
testing behind it — needs to know exactly where the line is between
"this is just the old recipe" (not patentable, protected as
**traditional knowledge**) and "this is a real, new invention built on
top of the old recipe" (patentable). Getting this wrong wastes money,
delays a product launch, or — worse — leads to a company mistakenly
believing it owns a monopoly it never legally had.

### The Biodiversity Trap: the Biological Diversity Act

Separately, India has a law called the **Biological Diversity Act, 2002**
(updated by a **2023 Amendment** and new **2024 Rules**) that protects
India's *biological resources* — its plants, its microbes, its genetic
material — from being taken and exploited without permission.

If your Ayurvedic product uses a biological resource sourced from India
(say, a specific plant extract), and you want to:
- **sell it commercially**, or
- **apply for a patent based on it** (in India or abroad),

...you may need **prior approval from the National Biodiversity Authority
(NBA)** — a government body — *before* you do that. Skipping this step
isn't just a paperwork miss; it's a real legal violation with real
penalties, and it can also **derail a patent application** if the patent
office discovers the required NBA approval was never obtained.

The tricky part: there isn't one single "biodiversity form." There are
**several different forms for several different situations** — one for
accessing a biological resource for research, a different one for
commercial use, a different one again for applying for a patent based on
it. Picking the *wrong* form, or not knowing a form is needed at all, is
an extremely easy and extremely costly mistake.

### Why a normal AI chatbot (like ChatGPT) is dangerous here

Ask a general-purpose AI chatbot a legal question like "can I patent
Triphala churna?" and it will *sound* confident and give you an answer —
but general AI models learn patterns from huge amounts of internet text,
not from being connected to the actual current law. They will sometimes
**invent a section number that doesn't exist**, cite a law that's been
repealed, or blend Indian law with a completely different country's law
without telling you. This isn't a rare glitch — independent studies of
commercial "AI legal assistant" tools found they gave a wrong,
overconfident answer **17–33% of the time**, even when they were built
using the same "search documents, then answer" technique this project
uses.

For a hobby chatbot, a wrong answer is embarrassing. For someone deciding
whether to spend months and money filing a patent, or whether they need
government approval before selling a product, **a confidently wrong
answer is worse than no answer at all** — it can lead to a real legal or
financial mistake. This is the exact problem IP-SAKTI Sahayak exists to
solve.

---

# 2. What is IP-SAKTI Sahayak? (The Product)

**IP-SAKTI Sahayak** ("Sahayak" means "helper/assistant" in Hindi) is an
AI assistant, built for Smart India Hackathon 2026 (problem statement
**SIH26045**, for the **Ministry of AYUSH**), that answers Ayurveda-related
IP and regulatory questions — but with a hard rule baked into its design:
**it is only allowed to answer using text it actually retrieved from a
fixed set of real government documents and treaties**, and it must show
you exactly which document, page, and section every part of its answer
came from. If the documents don't contain the answer, it says so plainly
instead of guessing — this is called **safe abstention** (see below).

It is not a general chatbot with an Ayurveda skin. It is closer to a
**very well-organized legal research assistant** that has actually read
30 real legal documents cover to cover, always shows its work, and
refuses to bluff.

### Key capability 1 — The Jurisdiction Switch

IP law works differently depending on *where* you are. India has its own
laws (the Patents Act, the Biological Diversity Act...). Separately,
there are **international treaties** — agreements between countries —
like the **WIPO Treaty on Intellectual Property, Genetic Resources and
Associated Traditional Knowledge (2024)**, often shortened to "WIPO
GRATK," the **Nagoya Protocol**, the **Budapest Treaty**, and the
**Patent Cooperation Treaty (PCT)**.

Mixing these up is a real risk: a rule from an international treaty
doesn't automatically apply inside India the same way, and vice versa.
So this system has an explicit switch — **India** or **International** —
and it *never* blends the two answer sets together. If you ask an
India-jurisdiction question, you only get Indian law back. Ask an
international one, and you only get treaty text back. (It *can* still
point out "hey, there's a related provision in the other jurisdiction" as
a separate, clearly-labeled cross-reference — but it never mixes them
into one answer.)

### Key capability 2 — The Formulation Classifier

Not every Ayurvedic product is regulated the same way. This system
automatically figures out which of six regulatory categories a question
is probably about:

| Category | What it means |
|---|---|
| **Classical** | A formulation straight from a recognized old text (like Triphala) — largely traditional knowledge, blocked from patenting by Section 3(p), but defendable using the **TKDL** (see below). |
| **Proprietary (P&P)** | A branded medicine with the company's own specific formulation — has real patent potential if it's genuinely new. |
| **Phytopharmaceutical** | A standardized, scientifically purified plant-based drug — its own regulatory lane under the Drugs & Cosmetics Rules. |
| **Ayurveda-Aahar / Nutraceutical** | A food product with health claims — regulated by the food safety authority (FSSAI), not the drug regulator. |
| **Cosmetic** | A skincare/beauty product — again, its own rules, and it legally cannot make medical claims. |
| **New / Non-Classical Drug** | A genuinely new drug needing real clinical trial evidence before anyone can sell it. |

Getting the category right changes *everything* about what rules apply —
so the system asks itself this question first, before trying to answer
anything else.

### Key capability 3 — Form & Registry Navigator

Beyond just explaining the law, the system points you at the **actual
government form** you likely need next — for example:

- **NBA Form 7** — prior approval from the National Biodiversity Authority
  before applying for a patent based on an Indian biological resource.
- **NBA Form 2** — approval for commercial use of a biological resource.
- **IPO Form 18A** — request for expedited (fast-tracked) patent
  examination at the Indian Patent Office.

Each of these came with a real correction worth knowing about: an earlier
draft of this feature assumed the patent-approval form was called "Form
3" — but checking it against the **actual, currently-indexed government
Rules text** (not guesswork, not general AI knowledge) showed the real
form is **Form 7**. This system is built to always double-check itself
against real source text rather than trust an assumption, even its own.

### Key capability 4 — Multilingual Voice Loop

You can talk to it and it can talk back, in multiple languages —
English, Hindi, Tamil, and more — using **Sarvam AI's** two speech
models:
- **Saaras** turns your spoken question into text (speech-to-text / ASR).
- **Bulbul** turns the written answer back into spoken audio
  (text-to-speech / TTS).

### Key capability 5 — The Safety Guardrail (Abstention)

If nothing in the indexed documents actually answers your question — say
you ask about baking a cake, or a completely unrelated country's tax
law — the system says, plainly: **"I could not find this in my
sources"**, and asks a clarifying question instead of guessing. This is
the single most important design decision in the whole project: **a
system that admits "I don't know" is more trustworthy than one that
never does.**

---

# 3. Plain-English Architecture (How It Actually Works)

Here's the journey one question takes, start to finish:

```
 You (voice or text)
        │
        ▼
 ┌─────────────────┐
 │    Frontend      │   The website you see and click on (Next.js/React)
 └─────────────────┘
        │  sends your question over the internet (an HTTP request)
        ▼
 ┌─────────────────┐
 │  FastAPI Server  │   The "front door" of the backend — receives the
 └─────────────────┘   request, decides which internal function handles it
        │
        ▼
 ┌───────────────────────────┐
 │   LangGraph Orchestrator   │   A fixed, step-by-step assembly line
 │  (rewrite → classify →     │   (explained below) that runs the same
 │   retrieve → rerank →      │   sequence of steps every single time —
 │   generate → cite)         │   nothing is left to chance
 └───────────────────────────┘
        │
        ▼
 ┌───────────────────────────┐
 │   Hybrid Search            │   Two different search methods running
 │  (BM25 + Dense Embeddings)  │   side by side, then combined fairly
 └───────────────────────────┘
        │
        ▼
 ┌───────────────────────────┐
 │   Quality Check             │   A second, stricter AI model double-checks
 │  (Cross-Encoder Reranker)   │   which search results are ACTUALLY relevant
 └───────────────────────────┘
        │
        ▼
 ┌───────────────────────────┐
 │   LLM Generation            │   The language model writes an answer —
 │  (Groq, multi-key backup)   │   but ONLY using the text handed to it above,
 └───────────────────────────┘   never its own general knowledge
        │
        ▼
 ┌───────────────────────────┐
 │  Knowledge Graph & Forms    │   Adds "see also" links to related treaties
 │  Enrichment                 │   and "you'll probably need this government
 └───────────────────────────┘   form" suggestions
        │
        ▼
 ┌─────────────────┐
 │  Audio Response   │   (optional) the answer is read aloud in your language
 └─────────────────┘
        │
        ▼
     Back to you, with every claim showing its exact source
```

Now let's unpack the concepts that sound scary but are actually simple
ideas wearing technical names:

**What is a Vector Embedding?**
Imagine turning every sentence into a point on a giant map, positioned so
that sentences with *similar meaning* end up near each other on the map
— even if they use completely different words. "Turmeric heals wounds"
and "curcuma is used for injury treatment" would land close together,
even though they share almost no words. This is like a library where
books are shelved by *topic and meaning*, not by the first letter of the
title.

**What is BM25?**
This is the opposite, more old-fashioned approach: exact keyword
matching, like pressing **Ctrl+F** in a document, but smarter (it
weighs rare, specific words like "3(p)" much more heavily than common
words like "the"). It's essential for legal text because section numbers
and exact legal terms need to be matched *exactly*, not just
"similar-ish."

**What is RRF Fusion (Reciprocal Rank Fusion)?**
We run *both* searches above at once, then fairly combine their two
separate top-20 result lists into one list — a document that ranks well
on *both* lists rises to the very top. It's like combining two different
judges' scorecards in a competition instead of trusting only one judge.

**What is LangGraph?**
Some AI systems are built as an "agent" that can freely decide what to do
next, in any order, looping as much as it wants — powerful, but
unpredictable and hard to debug, like letting an intern improvise a
process from scratch every time. **LangGraph**, as used here, is the
opposite: a **fixed assembly line**. Step 1 always happens, then step 2,
then step 3, in the same order, every single time. There's exactly *one*
allowed exception (if the first search attempt looks weak, it's allowed
to retry the search *once* with reworded terms) — never an unbounded
loop. This makes the system's behavior traceable and predictable, which
matters enormously in a legal-information tool.

**What is Abstention?**
The deliberate choice to say "I don't know" rather than make something
up. It sounds like a small feature, but it's the single biggest reason
this system is safer than a general chatbot — the wisdom to admit
uncertainty instead of confidently lying.

**What is Multi-Key Groq Rotation?**
The AI model that writes the answers (**Groq**, a fast AI hosting
service) gives free accounts a daily "fuel tank" of usage before it cuts
you off for the day. If you only have one fuel tank and it runs dry,
your car stops. This project keeps a **second and third fuel tank**
(backup API keys) in the trunk — the moment tank #1 runs dry, it
automatically switches to tank #2 without you noticing or needing to
restart anything.

---

# 4. Tour of the Codebase (File-by-File Breakdown)

### Backend (`backend/`) — the "brain," written in Python

| File / folder | What it does | What breaks if you delete it |
|---|---|---|
| `backend/run.py` | The actual "start the server" button on Windows. | The server won't start correctly on Windows (a technical event-loop conflict). |
| `backend/ingestion/loader.py` | Opens every PDF and pulls out the raw text, page by page. | Nothing gets indexed — the system would have no documents to search. |
| `backend/ingestion/chunker.py` | Cuts each document into small, labeled pieces ("chunks") — e.g. each numbered legal clause becomes its own piece. | Answers would lose their precise page/section citations. |
| `backend/ingestion/indexer.py` | Turns every chunk into a searchable "vector embedding" and builds the keyword (BM25) search index. | There would be nothing to search — every question would fail. |
| `backend/retrieval/bm25_search.py` | Does the exact-keyword search (the "Ctrl+F" search). | Exact section-number searches would get much worse. |
| `backend/retrieval/dense_search.py` | Does the meaning-based (vector) search. | Rephrased/conceptual questions would get much worse. |
| `backend/retrieval/fusion.py` | Fairly combines the two search results above. | One search method would unfairly dominate the other. |
| `backend/retrieval/reranker.py` | The stricter "quality check" AI that re-scores results for real relevance. | Irrelevant documents could slip through to the answer-writer. |
| `backend/generation/prompts.py` | The exact instructions given to the AI model — "only use the text below, and say so if it's not there." | The AI could start answering from its own general knowledge — the core safety feature would be gone. |
| `backend/generation/llm_client.py` | Talks to the Groq AI model (with the multi-key backup system) and, optionally, a local backup model (Ollama). | No answers could be generated at all. |
| `backend/generation/citation.py` | Builds the "Sources" list from the real documents that were actually used — the AI itself never writes this. | Citations could become fake/unverifiable — a critical safety feature lost. |
| `backend/graph/*.py` | The LangGraph "assembly line" wiring — the fixed step-by-step sequence described in Section 3. | The system would lose its predictable, traceable structure. |
| `backend/graph_kg/*.py` | The "knowledge graph" — links a domestic Indian law clause to its real international-treaty counterpart. | The "see also, here's the international version" feature would disappear. |
| `backend/compliance/form_navigator.py` | The catalog of real NBA/IPO government forms and the logic that matches a question to the right one. | The "you'll need this form" suggestions would disappear. |
| `backend/api/main.py` | The FastAPI "front door" — every URL (`/query`, `/health`, etc.) the frontend talks to is defined here. | The whole backend would stop responding to any request. |
| `backend/api/asr.py` | Speech-to-text — sends your recorded voice to Sarvam's Saaras model, gets text back. | Voice input would stop working. |
| `backend/api/tts.py` | Text-to-speech — sends the answer to Sarvam's Bulbul model, gets spoken audio back. | Voice output would stop working. |
| `backend/api/translation.py` | Translates your question into English before search, and the answer back into your language after. | Non-English questions would no longer be understood correctly. |
| `backend/scripts/evaluate_pipeline.py` | Runs a batch of test questions to measure real-world accuracy and speed. | You'd have no automated way to *prove* the system's accuracy. |
| `backend/scripts/test_e2e_integration.py` | A full "does everything actually work together" check, runnable in one command. | You'd have to manually re-test every feature by hand after any change. |
| `backend/tests/` | 63+ automated tests that run the *real* system (not fake/pretend data) to catch anything broken. | Bugs could silently creep back in without anyone noticing. |
| `data/` | The 30 real source PDFs (Indian Acts + international treaties) — the *only* things the system is allowed to answer from. | The system would have nothing to cite — it would abstain on every single question. |

### Frontend (`frontend/`) — the website, written in TypeScript/React

| File | What it does | What breaks if you delete it |
|---|---|---|
| `frontend/src/app/page.tsx` | The very first landing screen (choose your jurisdiction). | You couldn't start a session — the app wouldn't have an entry point. |
| `frontend/src/app/chat/page.tsx` | The actual chat screen. | There would be no chat interface at all. |
| `frontend/src/components/ChatView.tsx` | The "brains" of the chat screen — sends your question, handles the streaming answer, records your voice. | Nothing would actually happen when you typed or spoke a question. |
| `frontend/src/components/ChatMessageBubble.tsx` | Draws one message bubble — the answer text, confidence badge, sources, related laws, and forms. | Answers would show as plain unformatted text with no sources visible. |
| `frontend/src/components/CitationCard.tsx` | One clickable source citation. | You couldn't click through to see the original PDF page. |
| `frontend/src/components/ActionableFormCard.tsx` / `ActionableFormModal.tsx` | The clickable government-form suggestion card and its full-detail popup. | You wouldn't see which forms you need, or how to file them. |
| `frontend/src/lib/api.ts` | The **only** file that actually talks to the backend over the network. | Nothing on the whole site would be able to fetch an answer. |
| `frontend/src/lib/types.ts` | The exact shape of every piece of data going back and forth, written to match the backend precisely. | Small backend changes could silently break the frontend in confusing ways. |

---

# 5. Step-by-Step Local Setup Guide (Absolute Beginner Friendly)

### Prerequisites (install these first)

1. **Python 3.11** — download from [python.org](https://python.org). During install on Windows, tick "Add python.exe to PATH."
2. **Node.js 18 or newer** (this project was built and tested against **Node 20+**) — download from [nodejs.org](https://nodejs.org). This also installs `npm`.
3. **Git** — download from [git-scm.com](https://git-scm.com).

Check they installed correctly by opening a terminal (Command Prompt,
PowerShell, or Terminal) and typing:
```bash
python --version
node --version
git --version
```
Each should print a version number, not an error.

### Step 1 — Clone the project and move into it

```bash
git clone https://github.com/ayushanand27/sih-2026.git
cd sih-2026
```

### Step 2 — Set up the backend's Python environment

A **virtual environment** is a private, isolated box of Python packages
just for this project, so it doesn't interfere with anything else on
your computer.

```bash
cd backend
python -m venv .venv

# Activate it — Windows:
.venv\Scripts\activate
# Activate it — Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

You'll know it worked if your terminal prompt now shows `(.venv)` at the
start.

### Step 3 — Set up your `.env` file (your private keys)

```bash
cp env.example.txt .env
```
Now open the new `.env` file in any text editor and fill in:

| Variable | What it is | Where to get it |
|---|---|---|
| `DATABASE_URL` | A Postgres database connection string, with the `pgvector` extension enabled | Free tier at [neon.tech](https://neon.tech) or [supabase.com](https://supabase.com) |
| `GROQ_API_KEY` | Your AI model API key | Free at [console.groq.com](https://console.groq.com) |
| `GROQ_API_KEY_2`, `GROQ_API_KEY_3` | Optional backup keys, auto-used if the first one runs out for the day | Same place — just make a second/third free account |
| `SARVAM_API_KEY` | Powers translation, text-to-speech, and speech-to-text | Free at [dashboard.sarvam.ai](https://dashboard.sarvam.ai) |

Everything else in `.env` already has a sensible default — you don't need
to touch it.

### Step 4 — Index the documents and start the backend

Still inside `backend/`, with the virtual environment active:
```bash
python -m ingestion.indexer --reset
python -m graph_kg.build_kg
python run.py
```
The first command reads every PDF in `data/` and builds the search
indexes (takes a few minutes — it's genuinely processing 30 real
documents). The second builds the small "related laws" knowledge graph.
The third actually starts the server.

Open **http://localhost:8000/docs** in your browser — you should see an
interactive page listing every available API endpoint. If you see that,
your backend is alive.

### Step 5 — Set up and start the frontend

Open a **new** terminal window (leave the backend running in the first
one), then:
```bash
cd sih-2026/frontend
npm install
cp .env.local.example .env.local
npm run dev
```
`.env.local` already defaults to pointing at `http://127.0.0.1:8000` —
exactly where your backend is running — so you shouldn't need to change
anything unless you moved the backend to a different port.

### Step 6 — Open it in your browser

Go to **http://localhost:3000**. You should see the IP-SAKTI Sahayak
landing screen. Pick a jurisdiction, click "Start asking questions," and
you're in.

---

# 6. How to Test & Prove It Works (The Demo Guide)

### Three questions to type into the browser

**1. Domestic Section 3(p) test:**
> "Can I patent Triphala churna in India?"

What to look for: the answer should explicitly discuss **Section 3(p)**
of the Patents Act, cite a real source document, show a **classical**
formulation-category badge, and (if the underlying search happens to
surface a biological-resource-approval clause) may surface an **NBA
form** suggestion.

**2. International WIPO test:**
Switch the jurisdiction to **International**, then ask:
> "What are the disclosure requirements under the WIPO GRATK Treaty?"

What to look for: the answer should cite **Article 3** of the WIPO GRATK
Treaty specifically, and — importantly — should **not** mix in any
Indian domestic law citations. Jurisdictions are kept strictly separate.

**3. Trap query test (proving it doesn't hallucinate):**
> "Can I patent Paracetamol in Ayurveda?"

What to look for: an amber **"I could not find this in my sources"**
abstention banner, or a grounded, cited answer about the real Drugs &
Cosmetics Act ingredient-sourcing rule this exact question happens to
trigger — either way, never a fabricated statute number.

### Running the automated test suite

From `backend/`, with the virtual environment active:
```bash
python -m pytest -v
```
This runs **63 automated tests** against the real, live system (real
database, real AI calls) — not fake pretend data — checking everything
from search accuracy to citation correctness to API contract stability.
All 63 should print `PASSED`.

### Running the offline accuracy benchmark

```bash
python scripts/evaluate_pipeline.py
```
This runs a curated set of test questions through the real pipeline and
prints a table showing, for each one, whether the expected law was cited
and how long it took — then a summary: overall statutory accuracy, how
often it incorrectly refused to answer, and how often it correctly
refused an out-of-scope trap question. This is the evidence you'd show a
judge or evaluator to *prove* accuracy, not just claim it.

### Running the full end-to-end integration check

```bash
python scripts/test_e2e_integration.py
```
One command that exercises the entire system together — a real India
query, a real international query, the live-streaming answer connection,
a safety-abstention check, the PDF-source-serving endpoint, and the
voice-transcription endpoint's error handling — and prints a clear
PASS/FAIL line for each.

---

# 7. Common Troubleshooting

**Port 8000 or 3000 is already in use.**
Something else on your computer is already using that port. Either stop
that other program, or change the port: for the backend, run
`uvicorn api.main:app --port 8001` instead of `python run.py` (Windows
users, see the note in the README about why `run.py` is normally
preferred); for the frontend, run `npm run dev -- -p 3001` and update
`NEXT_PUBLIC_API_BASE_URL`/your browser URL to match.

**I'm getting a Groq "rate limit" / 429 error.**
Your Groq account has a daily usage limit on its free tier, and you've
used it up for the day. This project already handles this automatically
if you've set `GROQ_API_KEY_2` and `GROQ_API_KEY_3` in your `.env` — it
switches to the next key without you doing anything. If you only have one
key, either wait for the daily reset or add a free second account's key.

**My `.env` file's `DATABASE_URL` (or another variable) doesn't seem to
be taking effect — the app is trying to connect somewhere else entirely.**
This project was actually bitten by exactly this once during
development: if your computer *already* has a system-wide environment
variable with the same name (left over from a different, unrelated
project), it can silently override what's written inside `.env`. This
project defends against that specific problem —
`generation/llm_client.py` and every other module that reads `.env` calls
`load_dotenv(override=True)`, which forces this project's own `.env` file
to always win. If you still see this happen, check your operating
system's own environment variables (on Windows: search "Environment
Variables" in the Start menu) for a stray variable with the same name and
remove it.

**The frontend says "Could not reach the backend."**
Make sure `python run.py` is actually running in its own terminal window
and printed `Uvicorn running on http://0.0.0.0:8000` — and that
`frontend/.env.local`'s `NEXT_PUBLIC_API_BASE_URL` matches wherever it's
actually running.

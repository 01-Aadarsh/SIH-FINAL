# Quickstart — run on a fresh machine

Minimum commands to get both servers running from a clean clone.

## Prerequisites
- **Python 3.12** — not 3.13/3.14, `torch`/`sentence-transformers` don't
  support them yet. Check with `py -0p` (Windows) which versions you have.
- **Node.js 18+**
- A Postgres DB with the `pgvector` extension (a free
  [Neon](https://neon.tech) or [Supabase](https://supabase.com) project
  works — the app creates the extension itself on first connect)
- A free [Groq](https://console.groq.com) API key

## 1. Clone
```bash
git clone https://github.com/01-Aadarsh/SIH-FINAL.git
cd SIH-FINAL
```

## 2. Backend
```bash
cd backend
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy env.example.txt .env
```
Edit `.env` and fill in `DATABASE_URL` and `GROQ_API_KEY` (everything else
has a working default — see comments in `env.example.txt`).

Put the source PDFs in `backend/data/` (and international treaty PDFs in
`backend/data/international/`) — this repo doesn't ship them (see
`.gitignore`). Then index them:
```bash
python -m ingestion.indexer --reset
python -m graph_kg.build_kg
```

Start the API:
```bash
python run.py
```
Backend is now live at `http://127.0.0.1:8000` — leave this running.

## 3. Frontend (new terminal)
```bash
cd frontend
npm install
copy .env.local.example .env.local
npm run dev
```
Open **http://localhost:3000**.

## Verify it works
Ask: *"What does Section 3(p) of the Patents Act say about traditional
knowledge?"* — should return a cited answer, not an abstention.

## Running the test suite (optional, costs real Groq quota)
```bash
cd backend
python -m pytest -v
```
This hits the real DB and makes real Groq calls — not mocked. Don't run
it right before a demo if you're worried about daily token quota.

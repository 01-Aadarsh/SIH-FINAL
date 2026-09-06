# IP-SAKTI Sahayak — Frontend

Next.js + TypeScript + Tailwind chat UI for the IP-SAKTI Sahayak RAG backend.
Built directly against [`docs/API_CONTRACT.md`](../docs/API_CONTRACT.md) —
read that first if you're changing how this talks to the backend.

## Preview

**http://localhost:3000** — once you've run `npm run dev` (see below). There
is no live/hosted deployment yet, so this only works while the dev server is
running on your own machine.

## Running locally

```bash
cd frontend
npm install
cp .env.local.example .env.local   # adjust NEXT_PUBLIC_API_BASE_URL if needed
npm run dev
```

Opens on `http://localhost:3000`. The backend must be running separately
(see `../backend/`) at the URL in `NEXT_PUBLIC_API_BASE_URL`
(`http://127.0.0.1:8000` by default) — the header shows a live
online/unreachable indicator polling `GET /health`.

## What's here

- **Step 0 intake** (`src/components/IntakeScreen.tsx`) — jurisdiction
  (required: India / International) and formulation category (optional,
  free-text-equivalent from a fixed list) collected before the first
  question, per the contract's requirement that jurisdiction always be sent
  as one of the two exact strings the backend expects.
- **Chat view** (`src/components/ChatView.tsx`) — sends `history` (prior
  turns, current question excluded) with every request; renders citations,
  confidence badges, and abstentions (`flags.abstained`) distinctly. Never
  string-matches the answer text to detect abstention.
- **Source viewer** (`src/components/SourceViewer.tsx`) — split-screen panel
  that opens a citation's exact PDF page via the backend's `/sources`
  static mount.
- **Loading state** (`src/components/LoadingState.tsx`) — answers can
  legitimately take up to ~60s (cold model load, Ollama-timeout-then-Groq
  fallback); this shows elapsed time and rotating status text instead of a
  bare spinner that looks broken after 10 seconds.
- **API client** (`src/lib/api.ts`) — client-side fetch timeout at 68s (just
  above the backend's 60s `REQUEST_TIMEOUT`), and error handling for the
  documented `422` / `503` / `504` / `500` shapes.

## Known gaps

- No automated tests yet.
- No production `CORS_ORIGINS` value to point at — see the contract, this
  hasn't been deployed.

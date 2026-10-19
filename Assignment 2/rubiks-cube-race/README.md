# Rubik's Cube Race

Races four AI competitors on a **real 3x3 cube** (pycuber engine, every move validated) and compares them.

| # | Competitor | OpenRouter id | API |
|---|---|---|---|
| 1 | Jev 1.13 | `typesafe/jev-1.13` | Decisions API `POST /api/alpha/decisions` |
| 2 | GPT-6 Luna Decisions | `openai/gpt-6-luna-decisions` | Decisions API |
| 3 | Cloudflare Clef | `cloudflare/clef` | Decisions API |
| 4 | Claude Opus 5.5 | `anthropic/claude-opus-5.5` | Chat Completions `POST /api/v1/chat/completions` |

Stack: Python, FastAPI, Uvicorn, pycuber, HTTPX, python-dotenv, Pytest, vanilla HTML/CSS/JS, Chart.js (vendored).
See [docs/AUDIT.md](docs/AUDIT.md) for the requirement-by-requirement audit and [docs/MODEL_VERIFICATION.md](docs/MODEL_VERIFICATION.md).

## The experiment rules (all enforced in the backend)

* **Start state:** exactly **3 moves** from solved, *proven* by an exact shortest-path search plus an independent
  exhaustive pycuber check (so `R U U'` or `R R R` are rejected). Fixed by `OFFICIAL_SEED`, so every model, every
  restart and every rerun uses the same cube.
* Every model gets the same state JSON and equivalent instructions; **nobody is told the scramble**.
* One move per request; each reply is validated, only legal moves are applied. A model stops **immediately** when the
  real cube is solved or after **20** valid moves (then it is *unsolved*; optimality gap = N/A).
* **API budget** (persistent in `state/budget_ledger.json`, never reset by restart / Reset button / cache clear):
  total target **65** (code ceiling **100**), Claude **5** (failed requests and retries count), others 20 each
  (3 x 20 + 5 = 65). A request is counted and saved to disk **before** the network attempt. Over budget -> nothing is sent.
* **Persistent cache** (`state/response_cache.json`): checked before every request; key = SHA-256 of provider +
  endpoint + model id + the complete request payload. Cache hits cost nothing and are not API calls. Unusable replies
  (empty, reasoning-only, truncated, illegal move, malformed) are never cached.
* Live runs need the key, the `final` category, the official start state and a typed confirmation (`RUN LIVE`).
* Recorded per model: solve status, moves, live latency, **cache latency (separate)**, solve time, optimality gap,
  API calls, cache hits, provider-reported cost (never estimated; "not reported" if missing).

Modes: **mock** (simulation, free) · **ollama** (local models, free, never touches OpenRouter) ·
**replay** (the real adapters, cache only, 0 requests, no key) · **real** (OpenRouter, budget-protected).

## Install (Windows PowerShell)

```powershell
git clone <your-repo-url> rubiks-cube-race
cd rubiks-cube-race
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # edit .env: STUDENT_NAME, ROLL_NUMBER (key only on experiment day)
python -m pytest              # offline tests, no key, no network
python -m uvicorn backend.main:app
```
macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`, `cp .env.example .env`, same commands.
Open http://127.0.0.1:8000 . Run **one** server only. Do not use `--reload` during the live run.

## Free testing with Ollama (do most experiments here)

```bash
ollama pull llama3.2 ; ollama pull qwen2.5:3b ; ollama pull gemma2:2b ; ollama pull phi3
# in .env: OLLAMA_MODEL_JEV=llama3.2  OLLAMA_MODEL_LUNA=qwen2.5:3b  OLLAMA_MODEL_CLEF=gemma2:2b  OLLAMA_MODEL_OPUS=phi3
```
In the UI choose **Final** + **Ollama** + Run Benchmark. (Use different models per slot: identical requests are served once.)
These are stand-ins and are labelled "ollama"; they are not results for the real models.

## The single live OpenRouter experiment

1. Put the key in `.env` as `OPENROUTER_API_KEY=...` (never commit/share it), restart the server.
2. Rehearse first with Mock/Ollama. Check the budget panel reads 0 / 65 and Claude 0 / 5.
3. UI: **Final** + **Real** + Run Benchmark, type `RUN LIVE`. (API: `POST /api/benchmark {"category":"final","mode":"real","confirm_live":true}`.)
4. Worst case: 65 requests. Afterwards, rerunning is free (cache). To demo without risk: mode **Replay**.
5. Keep the `state/` folder and `results/` (do not delete them); record the screen video (docs/DEMO_VIDEO_CHECKLIST.md).

## API

`GET /` · `GET /api/health` · `GET /api/models` · `POST /api/scramble` · `POST /api/benchmark` · `GET /api/results`
· `GET /api/benchmark/{id}` · `GET /api/budget` (read-only counters, cache stats) · `GET /api/config` · `POST /api/reset`
(`/api/reset` only clears the cube view; no endpoint can reset the budget or cache).

## Tests

`python -m pytest` — 217 offline tests (mocked HTTP, no key). Covers moves, exact-3 proof, budget limits, restarts,
concurrency, cross-process lock, cache, retries, timeouts, invalid/empty/reasoning-only replies, key leakage,
Ollama isolation, API gating.

## Security

`.env` is git-ignored; the key is never returned by any endpoint, logged, cached or saved in results.

# Demonstration video checklist

Record your screen with your voice. Keep it to about 5-8 minutes. Tick each item as you show it.

## Before recording
- [ ] Put your real name and roll number in `.env` (`STUDENT_NAME`, `ROLL_NUMBER`) and restart the server.
- [ ] `.venv` is active, `pytest` has just passed (keep the terminal output).
- [ ] Browser at `http://127.0.0.1:8000`, window wide enough to show the whole page.
- [ ] (Optional, only if you have a key) `OPENROUTER_API_KEY` in `.env` and a few credits.

## 1. Introduction (30 s)
- [ ] Say the project name, your name and roll number (visible in the page header).
- [ ] State the four competitors: Jev 1.13, GPT-6 Luna Decisions, Cloudflare Clef, Claude Opus 5.5.

## 2. Setup and tests (1 min)
- [ ] Show the folder layout: `backend/`, `backend/models/`, `frontend/`, `tests/`, `results/`.
- [ ] Run `python -m pytest` and show the passing summary.
- [ ] Start the backend with `uvicorn backend.main:app` and open `/api/health` and `/docs`.

## 3. The cube and scrambles (1.5 min)
- [ ] Click **Generate Scramble** for Warm-up (3-4), Primary (4-5) and Extension (15-20); point out the move count.
- [ ] Drag the 3D cube and show the unfolded net; explain they come from the real cube state (pycuber).
- [ ] Explain the counting convention: every one of the 18 moves (R, R', R2 ...) counts as one move.

## 4. Benchmark (2 min)
- [ ] Press **Run Benchmark** (Mock mode). Show the four cards going `thinking` -> `solved/failed`.
- [ ] Point out: the same start state for all, move count, latency, solve time, invalid-move counter.
- [ ] Open the **Move history** tab of one competitor; show a rejected invalid move and the 30-move limit failure.
- [ ] Use the replay slider to step through a competitor's moves on the cube.
- [ ] Say clearly: Mock results are simulations.

## 5. Results (1 min)
- [ ] Show the four charts (latency, moves, solve time, success rate) and the final results table.
- [ ] Explain the optimality gap: shown only when the exact shortest solution is known (up to 8 moves), otherwise N/A.

## 6. Real models (1 min)
- [ ] Show the "Competitors" section: model ids and the Decisions API vs chat protocol.
- [ ] If you have a key: switch to **Real**, run Warm-up, show the real results. If not: say honestly that
      Real mode needs an OpenRouter key and show the 400 error message when it is missing.
- [ ] Mention `docs/MODEL_VERIFICATION.md`.

## 7. Wrap-up (30 s)
- [ ] Show `.gitignore` / `.env.example` (no secrets committed) and the GitHub repository page.
- [ ] Mention one limitation (real models not tested without a key, alpha Decisions API).

# Rubik's Cube Race Code

Races four AI competitors on a **real 3x3 cube** (pycuber engine, every move validated) and compares them.

| # | Competitor | OpenRouter id | API |
|---|---|---|---|
| 1 | Jev 1.13 | `typesafe/jev-1.13` | Decisions API `POST /api/alpha/decisions` |
| 2 | GPT-6 Luna Decisions | `openai/gpt-6-luna-decisions` | Decisions API |
| 3 | Cloudflare Clef | `cloudflare/clef` | Decisions API |
| 4 | Claude Opus 5.5 | `anthropic/claude-opus-5.5` | Chat Completions `POST /api/v1/chat/completions` |

Stack: Python, FastAPI, Uvicorn, pycuber, HTTPX, python-dotenv, Pytest, vanilla HTML/CSS/JS, Chart.js (vendored).

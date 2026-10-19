# Model verification (checked 2026-10-16 / 2026-10-17)

The assignment names four competitors. Names are **not** API identifiers, so each one was looked up on
OpenRouter's public model pages and API reference before any adapter was written.

| Assignment name | Verified OpenRouter id | API protocol | Source |
|---|---|---|---|
| Jev 1.13 | `typesafe/jev-1.13` | **Decisions API** `POST /api/alpha/decisions` | https://openrouter.ai/models/typesafe/jev-1.13 |
| GPT-6 Luna Decisions | `openai/gpt-6-luna-decisions` | **Decisions API** | https://openrouter.ai/models/openai/gpt-6-luna-decisions |
| Cloudflare Clef | `cloudflare/clef` | **Decisions API** | https://openrouter.ai/cloudflare/clef |
| Claude Opus 5.5 | `anthropic/claude-opus-5.5` | Chat completions `POST /api/v1/chat/completions` | https://openrouter.ai/models/anthropic/claude-opus-5.5-20260921 |

Request/response format of the Decisions API:
https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request

## What this means for the benchmark

* Jev, Luna and Clef are **decision models**, not chat models. OpenRouter states that chat-completions SDKs do not
  work with them. They take a `state` and typed `questions` and return probabilities. Our adapter sends one `choice`
  question (`next_move`) whose options are the 18 legal moves, and reads `answers.next_move.choice`.
* Claude Opus 5.5 is an ordinary chat model, so it is asked (system prompt + current state) to reply with JSON
  `{"move": "R'"}`.
* Every model is shown the same information: the current 54-sticker state, notation, its own move history and the
  remaining move budget. **No model is told the scramble.**
* The served model name in each reply is compared with the requested one. A different model aborts that competitor
  (`model_mismatch`); nothing is silently swapped. Date suffixes (e.g. `-20260918`) are accepted.
* OpenRouter also lists Jev 1.14 (newer). The assignment says 1.13, so 1.13 is used.

## Honest limitations

* **No real model has been called from this repository's development environment**: there was no OpenRouter key.
  The adapters are tested only against a fake HTTP transport (`tests/test_adapters_http.py`) built from the documented
  request/response shapes. The first real call with your key is the true integration test; if OpenRouter's alpha
  Decisions API has changed, fix `backend/models/decisions.py`.
* The Decisions API is marked **alpha** by OpenRouter and may change.
* Decision models were not designed to solve cubes by reading a facelet string; weak results are plausible and are
  reported as they are. Nothing is tuned to make a model look good.
* Cloudflare's Workers AI provider truncates long text state to ~2K tokens. Our state is much shorter.
* Mock competitors are **simulations** with invented personalities (speed, error rate). They say nothing about the real models.

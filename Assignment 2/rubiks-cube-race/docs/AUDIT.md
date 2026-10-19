# Audit against the professor's requirements

Method: read the actual source, wrote tests for each rule, mutation-checked the budget tests (breaking the limit /
count-before-network / cache-hit accounting made the tests fail), ran the suite offline.
**Not done (by design):** no live OpenRouter or Claude request was made during the audit.

## Defects found and fixed
| # | Defect | Fix |
|---|---|---|
| 1 | `BudgetLedger._event(kind=...)` clash raised `TypeError` whenever a counted request *failed*: the real API error was masked and the failure/cost record lost (10 tests failing) | parameter renamed (`backend/budget.py`) |
| 2 | Stale tests/config (30-move limit, old categories) | updated to 20 / `final` |
| 3 | Claude's 5-cap keyed only by slot name | now also follows the model id (`guard.is_claude_model`) |
| 4 | Truncated (`finish_reason=length`) and loosely-worded prose replies could become moves | rejected (`chat.py`, `parsing.py`) |
| 5 | Claude saw a different state subset than the decision models | chat user message is now the identical state JSON |
| 6 | Live runs accepted any start state (cache miss = needless spend) | real mode only runs the official start state |
| 7 | Free-test runs could pollute aggregates; unexpected exceptions were silent | excluded from aggregates; traceback logged |
| 8 | Frontend lacked final category, Ollama/replay, budget panel, calls/hits/cost/cache-latency, live-run confirmation | added |
| 9 | Test helper `solver_move` signature bug | fixed |

## Requirement table (status = what was actually checked)
| Requirement | Status | Where | Evidence |
|---|---|---|---|
| Same initial state for all | PASS | `benchmark.create_run/execute_run` | `test_full_run_every_model_gets_the_same_start...` |
| Start exactly 3 moves, proven | PASS | `experiment.verify_exact_distance` | `test_final_start_is_exactly_three_moves_proven_two_independent_ways`; `R U U'`, `R R R`, `R L R'` rejected |
| Equivalent instructions/info | PASS | `prompts.state_payload` | `test_all_four_models_get_equivalent_information` |
| Stop on solve / 20 moves; unsolved marked; gap N/A | PASS | `benchmark.run_competitor` | `test_a_competitor_stops_immediately...`, worst-case test |
| Every move validated before applying | PASS | `cube_engine.validate_move` | invalid/empty/reasoning/truncated/malformed tests: cube untouched |
| Claude <= 5 actual attempts incl. failures/retries/restarts/concurrency | PASS | `budget.py`, `guard.py` | 40 concurrent -> 5 sent; separate processes -> 5; restart -> still 5 |
| 65 target, 100 ceiling | PASS | `budget.Limits` | worst-case full run = exactly 65 fake requests; env 999 clamps to 100 |
| Counted before the network attempt; failures/timeouts count | PASS | `guard._live` | file read during `send` shows count; timeout/cancel/500 counted |
| Cache checked before every request; persistent; key = model + full payload | PASS | `cache.py`, `guard.execute` | restart test: 0 requests, 12 cache hits; key tests |
| Counters/cache never reset | PASS (design) | no reset endpoint; fail-closed ledger | `test_nothing_in_the_ui_api_can_reset...`, corrupt-ledger tests |
| Live vs cache latency separate; cache hit != API call | PASS | `metrics.py` | rerun: live latency None, cache latency set, api_calls 0 |
| Actual cost | PASS | `guard.reported_cost` | provider `usage.cost` only; missing -> "not reported" |
| Bounded retries, no loops, no duplicate concurrent calls | PASS | `benchmark.py`, `guard._inflight` | max 2 attempts on any failure; 10 identical concurrent -> 1 request |
| Ollama never reaches OpenRouter | PASS (mocked) | `ollama.py` | host check, ledger `openrouter.requests == 0` |
| Ollama with a real local model | NOT VERIFIED | | no Ollama in the audit sandbox; only the clean "not running" path and mocked HTTP were run |
| Decisions API / Chat payloads, endpoints, IDs | PARTIAL | `decisions.py`, `chat.py` | matches OpenRouter docs; tested vs fake server. Earlier runs on the student's own key reached Jev and Clef; Luna returned HTTP 502 "refused". **No live call from the audit.** |
| Key never exposed/logged/committed | PASS | `.gitignore`, `http.redact` | key-leak test (logs, state, results); no secrets in git history |
| UI shows state, history, status, latency, gap, calls, hits, cost | PASS | `frontend/` | headless-browser run, 0 JS errors |
| Screen recording; real experiment result | NOT DONE | | needs the student and the key |

## Remaining risks before the live run
1. Claude's reply format/timeouts are unproven live; a timeout still burns one of its 5 requests (timeout raised to 120 s).
2. Luna previously answered 502 "refused"; it will be retried once (2 requests) then reported unsolved.
3. Decision models are not built for cubes: unsolved is a legitimate result.
4. Delete `state/` and the counters restart: never do this; keep it in the zip you submit.

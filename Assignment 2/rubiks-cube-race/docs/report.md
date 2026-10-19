# Rubik's Cube Race — Project Report

## 1. Project Overview

**Rubik's Cube Race** is an AI benchmarking application that compares four AI competitors on solving a real 3×3 Rubik's Cube. Each competitor receives the same scrambled cube state, proposes moves, and has every move validated by the cube engine.

**Student:** Syeda Sara Ali  
**Roll No:** 23K-0070

## 2. AI Competitors

- **Jev 1.13** — TypeSafe decision model
- **GPT-6 Luna Decisions** — OpenAI decision model
- **Cloudflare Clef** — Open-weight decision model
- **Claude Opus 5.5** — Anthropic chat model

The application uses OpenRouter to communicate with the models.

## 3. How It Works

1. Generate a scramble for the cube.
2. Give the same starting state to all four competitors.
3. Request moves from each AI model.
4. Validate and apply each move to the cube.
5. Stop when the cube is solved, the move limit is reached, or repeated errors occur.
6. Compare results using solve status, move count, latency, API errors, and optimality gap.

The benchmark supports **Mock mode** for simulated testing and **Real mode** for API-based experiments.

## 4. Current Experiment Results

The latest displayed real-mode run used the scramble `R D2 B2`.

The shortest solution is **3 moves: `B2 D2 R'`**.

| Competitor | Moves Applied | Result |
|---|---:|---|
| Jev 1.13 | 20 | Unsolved — move limit reached |
| GPT-6 Luna Decisions | 2 | Failed — repeated HTTP 502 errors |
| Cloudflare Clef | 20 | Unsolved — move limit reached |
| Claude Opus 5.5 | 0 | Failed — insufficient OpenRouter credits (HTTP 402) |

**Result:** 0 out of 4 competitors solved the cube in this run.

## 5. Observations

- Jev and Clef applied valid moves but did not solve the cube within the 20-move limit.
- Luna encountered repeated provider/server errors while requesting its next move.
- Claude's request failed because the available OpenRouter credits were insufficient.
- These results do not establish that the models can never solve the cube; they describe this particular experiment.
- The scramble's known shortest solution provides a reference for measuring move efficiency.

## 6. Evaluation Metrics

The application tracks:

- Solve success or failure
- Number of valid moves applied
- Average API latency and total solve time
- Invalid moves and API errors
- Optimality gap relative to the shortest solution
- Saved-run success rates and aggregate performance

## 7. Conclusion

Rubik's Cube Race provides a controlled way to compare AI models on a shared, rule-based task. The current experiment highlights differences in move selection, reliability, and API availability. Further testing is needed to obtain successful solves and make stronger performance comparisons.

**Note:** Mock results are simulations and must not be presented as real API results. API failures and unsuccessful runs should remain part of the experiment record.

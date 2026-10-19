"""FastAPI application: REST API + static frontend.

Run with:  uvicorn backend.main:app --reload
"""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .benchmark import BenchmarkRun, create_run, execute_run
from .config import FRONTEND_DIR, Settings, get_settings
from .cube_engine import (
    CATEGORIES, COUNTING_CONVENTION, MOVES, CubeEngine, InvalidMoveError,
    category_range, generate_scramble_for_category, validate_move,
)
from .experiment import FINAL_DISTANCE, generate_final_scramble, verify_exact_distance
from .models.guard import get_guard
from .metrics import aggregate_runs, summarize_run
from .models import COMPETITOR_KEYS, MODES, SPECS, AdapterError, build_adapters, effective_spec
from .optimal import find_optimal, not_computed
from .schemas import BenchmarkRequest, ScrambleRequest
from .storage import ResultStore

MAX_LIVE_RUNS = 25


class RunManager:
    """Keeps recent runs in memory (for live polling) and saves finished ones to results/."""

    def __init__(self) -> None:
        self.runs: dict[str, BenchmarkRun] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def active(self) -> Optional[str]:
        for run_id, task in self.tasks.items():
            if not task.done():
                return run_id
        return None

    def remember(self, run: BenchmarkRun) -> None:
        self.runs[run.run_id] = run
        while len(self.runs) > MAX_LIVE_RUNS:
            oldest = next(iter(self.runs))
            if oldest in self.tasks and not self.tasks[oldest].done():
                break
            self.runs.pop(oldest)
            self.tasks.pop(oldest, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.manager = RunManager()
    yield
    for task in app.state.manager.tasks.values():
        task.cancel()


app = FastAPI(
    title="Rubik's Cube Race",
    description="Benchmarks four AI competitors on a real 3x3 Rubik's Cube.",
    version="1.0.0",
    lifespan=lifespan,
)


def error_body(code: str, message: str, details=None) -> dict:
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(error_body(f"http_{exc.status_code}", str(exc.detail)), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = [{"where": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(error_body("validation_error", "The request is not valid.", details), status_code=422)


def manager() -> RunManager:
    return app.state.manager


def store(settings: Settings) -> ResultStore:
    return ResultStore(settings.results_dir)


# --------------------------------------------------------------------------- pages / meta


@app.get("/", include_in_schema=False)
async def index():
    page = FRONTEND_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html is missing")
    return FileResponse(page)


@app.get("/api/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "service": "rubiks-cube-race",
        "version": app.version,
        "real_mode_available": settings.api_key_configured,  # the key itself is never returned
        "mock_mode_available": True,
        "modes": list(MODES),
        "budget_healthy": get_guard(settings).ledger.status()["healthy"],
        "move_limit": settings.move_limit,
        "active_run": manager().active(),
    }


@app.get("/api/config")
async def config():
    settings = get_settings()
    return {
        "student_name": settings.student_name,
        "roll_number": settings.roll_number,
        "move_limit": settings.move_limit,
        "categories": {k: {"min": lo, "max": hi} for k, (lo, hi) in CATEGORIES.items()},
        "moves": list(MOVES),
        "counting_convention": COUNTING_CONVENTION,
        "real_mode_available": settings.api_key_configured,
        "modes": list(MODES),
        "profile": settings.openrouter_profile,
        "official_seed": settings.official_seed,
        "final_distance": FINAL_DISTANCE,
        "ollama_models": settings.ollama_models,
        "live_requires_confirmation": True,
    }


@app.get("/api/models")
async def models():
    settings = get_settings()
    return {
        "real_mode_available": settings.api_key_configured,
        "profile": settings.openrouter_profile,
        "models": [
            {**effective_spec(k, settings).to_public_dict(), "real_available": settings.api_key_configured,
             "mock_available": True}
            for k in COMPETITOR_KEYS
        ],
    }


@app.get("/api/budget")
async def budget():
    """Persistent OpenRouter request counters, limits and cache status. Read-only; makes no network request."""
    settings = get_settings()
    guard = get_guard(settings)
    status = guard.ledger.status()
    lim = status["limits"]
    return {
        "ledger": status,
        "cache": guard.cache.stats(),
        "profile": settings.openrouter_profile,
        "planned_max_final_run": {
            "jev": lim["default_model_limit"], "luna": lim["default_model_limit"], "clef": lim["default_model_limit"],
            "opus": lim["claude_limit"],
            "total": 3 * lim["default_model_limit"] + lim["claude_limit"],
            "note": "Upper bound if every request is live. Cache hits cost nothing.",
        },
        "note": "Counters live in state/budget_ledger.json and survive restarts. Nothing in the UI can reset them.",
    }


# --------------------------------------------------------------------------- scramble


def cube_view(cube: CubeEngine) -> dict:
    return {**cube.to_dict(), "faces": cube.faces()}


@app.post("/api/scramble")
async def scramble(req: ScrambleRequest):
    settings = get_settings()
    seed = req.seed
    if seed is None and req.category == "final":
        seed = settings.official_seed  # the final experiment has ONE fixed start state, so reruns are cache hits
    rng = random.Random(seed) if seed is not None else random.Random()
    try:
        moves = generate_scramble_for_category(req.category, rng, req.length)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    cube = CubeEngine.from_scramble(moves)
    verification = verify_exact_distance(cube.facelets).to_dict() if req.category == "final" else {}
    low, high = category_range(req.category)
    optimal = find_optimal(cube.facelets) if len(moves) <= get_settings().optimal_max_scramble else not_computed(
        "Exact search only proves optimal lengths up to 8 moves (N/A for longer scrambles)."
    )
    return {
        "category": req.category,
        "category_range": {"min": low, "max": high},
        "seed": seed,
        "start_verification": verification,
        "scramble_moves": moves,
        "scramble": " ".join(moves),
        "scramble_length": len(moves),
        "cube": cube_view(cube),
        "optimal": optimal.to_dict(),
        "counting_convention": COUNTING_CONVENTION,
    }


@app.post("/api/reset")
async def reset():
    """A fresh solved cube (the server keeps no cube between requests).

    Deliberately does NOT touch the request ledger or the response cache: no UI action can reset the budget.
    """
    return {"cube": cube_view(CubeEngine.solved())}


# --------------------------------------------------------------------------- benchmark


def _prepare(req: BenchmarkRequest, settings: Settings):
    seed = req.seed if req.seed is not None else (
        settings.official_seed if req.category == "final" else random.randrange(2**31))
    low, high = category_range(req.category)
    guard = get_guard(settings)

    # --- live OpenRouter runs are gated: key, explicit confirmation, final category only
    if req.mode == "real":
        if not settings.api_key_configured:
            raise HTTPException(status_code=400, detail="Real mode needs OPENROUTER_API_KEY. Put it in your .env file "
                                                        "(see .env.example) and restart.")
        if not req.confirm_live:
            raise HTTPException(status_code=400, detail="A live OpenRouter run spends the restricted API budget and "
                                                        "must be confirmed explicitly (confirm_live=true). Use mode "
                                                        "'replay' to reuse cached answers for free.")
        if req.category != "final" and not settings.allow_live_non_final:
            raise HTTPException(status_code=400, detail="Live OpenRouter runs are limited to the 'final' category "
                                                        "(set ALLOW_LIVE_NON_FINAL=1 to override).")
    limit = req.move_limit or settings.move_limit
    if limit > settings.move_limit:
        raise HTTPException(status_code=422, detail=f"move_limit may not exceed {settings.move_limit}.")

    if req.moves is not None:
        try:
            moves = [validate_move(m) for m in req.moves]
        except InvalidMoveError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if req.mode == "real" and moves != generate_final_scramble(random.Random(settings.official_seed)):
            raise HTTPException(status_code=422, detail="Live runs only use the official start state (a different "
                                                        "start would miss the cache and spend budget). Omit 'moves'.")
        if not low <= len(moves) <= high:
            raise HTTPException(
                status_code=422,
                detail=f"A {req.category!r} scramble needs {low}-{high} moves; got {len(moves)}.",
            )
        if CubeEngine.from_scramble(moves).is_solved():
            raise HTTPException(status_code=422, detail="That scramble leaves the cube solved; nothing to race.")
    else:
        moves = generate_scramble_for_category(req.category, random.Random(seed))

    verification: dict = {}
    if req.category == "final":
        check = verify_exact_distance(CubeEngine.from_scramble(moves).facelets)
        if not check.verified:
            raise HTTPException(status_code=422, detail=f"The final experiment needs a start state exactly "
                                                        f"{FINAL_DISTANCE} moves from solved. {check.note}")
        verification = check.to_dict()

    keys = req.competitors or list(COMPETITOR_KEYS)
    unknown = [k for k in keys if k not in SPECS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown competitor(s): {', '.join(unknown)}")
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="Competitors must not repeat")
    try:
        adapters = build_adapters(req.mode, settings, seed=seed, keys=keys, guard=guard)
    except AdapterError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    run = create_run(category=req.category, scramble=moves, mode=req.mode, move_limit=limit, seed=seed,
                     adapters=adapters)
    run.start_verification = verification
    run.profile = settings.openrouter_profile if req.mode in ("real", "replay") else "n/a"
    if req.mode != "mock":
        run.budget_before = guard.ledger.status()
    return run, adapters


async def _execute_and_save(run: BenchmarkRun, adapters, settings: Settings) -> None:
    await execute_run(run, adapters, settings)
    if run.mode != "mock":
        run.budget_after = get_guard(settings).ledger.status()
    try:
        store(settings).save(run.to_dict())
    except OSError as exc:  # a full disk must not hide the result from the user
        run.error = (run.error or "") + f" (could not save result file: {exc})"


@app.post("/api/benchmark")
async def benchmark(req: BenchmarkRequest):
    settings = get_settings()
    mgr = manager()
    if mgr.active():
        raise HTTPException(status_code=409, detail=f"A benchmark is already running (run_id {mgr.active()}).")
    run, adapters = _prepare(req, settings)
    mgr.remember(run)
    task = asyncio.create_task(_execute_and_save(run, adapters, settings))
    mgr.tasks[run.run_id] = task
    if req.wait:
        await task
        return run.to_dict()
    return JSONResponse(run.to_dict(), status_code=202)


@app.get("/api/benchmark/{run_id}")
async def benchmark_status(run_id: str):
    run = manager().runs.get(run_id)
    if run is not None:
        return run.to_dict()
    saved = store(get_settings()).get(run_id)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id!r}")
    return saved


# --------------------------------------------------------------------------- results


@app.get("/api/results")
async def results(
    category: Optional[str] = Query(default=None, pattern="^(warmup|primary|extension|final)$"),
    mode: Optional[str] = Query(default=None, pattern="^(mock|ollama|replay|real)$"),
    limit: int = Query(default=50, ge=1, le=500),
):
    runs = [r for r in store(get_settings()).list()
            if (not category or r.get("category") == category) and (not mode or r.get("mode") == mode)]
    aggregate = [row for row in aggregate_runs(runs, category) if not mode or row["mode"] == mode]
    return {
        "count": len(runs),
        "runs": [summarize_run(r) for r in runs[:limit]],
        "aggregate": aggregate,
        "note": "Mock and real results are aggregated separately. Mock results are simulations.",
    }


@app.get("/api/results/{run_id}")
async def result_detail(run_id: str):
    saved = store(get_settings()).get(run_id)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"No saved run with id {run_id!r}")
    return saved


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR), check_dir=False), name="static")

"""Exact shortest-solution search (bidirectional breadth-first search).

Why this approach: the assignment's optimality gap is only meaningful when the true shortest solution
is KNOWN, so we never estimate it. Bidirectional BFS gives a proof:

* A table of every state within ``BACKWARD_DEPTH`` (4) moves of the solved cube is built once.
* From the start state we expand layer by layer (up to ``MAX_FORWARD_DEPTH`` = 4) and look for any
  state that is also in the table. The smallest ``forward + backward`` distance over all meetings is
  the exact shortest solution length (see the reasoning in ``find_optimal``).
* If nothing meets, the shortest solution is longer than 8 moves; we then report "unknown" (N/A)
  instead of guessing. 15-20 move scrambles are therefore reported as N/A, which is the honest answer
  without a full optimal solver.

Length is measured in the face-turn metric (all 18 moves count as 1), matching the benchmark.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from .cube_engine import MOVES, inverse_move, validate_facelets
from .fast_cube import SOLVED_STATE, apply_move, to_state

BACKWARD_DEPTH = 4
MAX_FORWARD_DEPTH = 4
MAX_EXACT_LENGTH = BACKWARD_DEPTH + MAX_FORWARD_DEPTH

_table_lock = threading.Lock()
_backward_table: Optional[dict[bytes, tuple[int, Optional[str]]]] = None


@dataclass(frozen=True)
class OptimalResult:
    """Outcome of the exact search.

    ``length``/``moves`` are ``None`` when the shortest solution is longer than ``MAX_EXACT_LENGTH``
    (``exact`` is then ``False``): the true value is unknown, never estimated.
    """

    length: Optional[int]
    moves: Optional[list[str]] = field(default=None)
    exact: bool = False
    method: str = "bidirectional-bfs"
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "length": self.length,
            "moves": self.moves,
            "exact": self.exact,
            "method": self.method,
            "note": self.note,
        }


def not_computed(note: str) -> OptimalResult:
    return OptimalResult(length=None, moves=None, exact=False, method="none", note=note)


def backward_table() -> dict[bytes, tuple[int, Optional[str]]]:
    """state -> (distance to solved, move that was applied to reach it from a state one step closer)."""
    global _backward_table
    with _table_lock:
        if _backward_table is None:
            table: dict[bytes, tuple[int, Optional[str]]] = {SOLVED_STATE: (0, None)}
            frontier = [SOLVED_STATE]
            for depth in range(1, BACKWARD_DEPTH + 1):
                next_frontier = []
                for state in frontier:
                    for move in MOVES:
                        new_state = apply_move(state, move)
                        if new_state not in table:
                            table[new_state] = (depth, move)
                            next_frontier.append(new_state)
                frontier = next_frontier
            _backward_table = table
        return _backward_table


def find_optimal(facelets: str) -> OptimalResult:
    """Exact shortest solution for ``facelets`` if it is at most 8 moves away, otherwise 'unknown'."""
    start = to_state(validate_facelets(facelets))
    bwd = backward_table()

    # parents: state -> (parent_state, move_from_parent) for rebuilding the forward half of the path
    parents: dict[bytes, Optional[tuple[bytes, str]]] = {start: None}
    best: Optional[tuple[int, bytes]] = None  # (total length, meeting state)
    if start in bwd:
        best = (bwd[start][0], start)

    frontier = [start]
    depth = 0
    # Correctness: let D be the true distance. Once forward layers 0..f and backward layers 0..4 are known,
    # every shortest path has a state at forward distance min(D, f) whose backward distance is
    # max(0, D - f) <= 4 whenever D <= f + 4. That state is in both tables, so the minimum of
    # forward + backward over all meetings equals D. We stop at the first layer that produces a meeting,
    # because a meeting at layer f means D <= f + 4 and all smaller options were already examined.
    while best is None and depth < MAX_FORWARD_DEPTH:
        next_frontier = []
        for state in frontier:
            for move in MOVES:
                new_state = apply_move(state, move)
                if new_state in parents:
                    continue
                parents[new_state] = (state, move)
                next_frontier.append(new_state)
                if new_state in bwd:
                    total = depth + 1 + bwd[new_state][0]
                    if best is None or total < best[0]:
                        best = (total, new_state)
        frontier = next_frontier
        depth += 1

    if best is None:
        return OptimalResult(
            length=None,
            moves=None,
            exact=False,
            note=f"Shortest solution is longer than {MAX_EXACT_LENGTH} moves; exact length unknown (N/A).",
        )

    total, meeting = best
    forward: list[str] = []
    state = meeting
    while parents[state] is not None:
        state, move = parents[state]  # type: ignore[misc]
        forward.append(move)
    forward.reverse()

    backward: list[str] = []
    state = meeting
    while bwd[state][0] > 0:
        move = bwd[state][1]
        assert move is not None
        undo = inverse_move(move)
        backward.append(undo)
        state = apply_move(state, undo)
    solution = forward + backward
    if len(solution) != total:  # internal consistency guard; should be impossible
        raise RuntimeError("Optimal search produced an inconsistent path length")
    return OptimalResult(length=total, moves=solution, exact=True, note="Exact: proven shortest by bidirectional BFS.")

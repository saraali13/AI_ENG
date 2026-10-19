"""The final experiment's starting cube: exactly 3 moves from solved, PROVEN (not just "3 moves applied").

Applying three random moves can cancel (``R U U'`` is one move) or collapse (``R R`` is ``R2``). So a scramble is only
accepted when its shortest solution is proven to be exactly ``FINAL_DISTANCE`` moves, by two independent checks:

1. Upper bound + exact value: ``optimal.find_optimal`` (bidirectional breadth-first search over a fast permutation
   engine) returns a solution of exactly 3 moves, and that solution is replayed on the pycuber-backed ``CubeEngine``
   to prove it really solves the cube.
2. Lower bound: every sequence of at most 2 moves (343 sequences) is applied to a fresh pycuber cube; the start state
   must NOT be any of them. This uses only pycuber, not the fast engine, so it is independent of check 1.

Distance is measured in the face-turn metric (all 18 moves count 1). The generator uses quarter turns only (no
``R2``), so the distance is also exactly 3 in the quarter-turn metric (QTM >= FTM and the scramble has 3 quarter turns).
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Optional

from .cube_engine import MOVES, CubeEngine
from .optimal import find_optimal

FINAL_DISTANCE = 3
QUARTER_TURNS = [m for m in MOVES if not m.endswith("2")]
_ball_cache: dict[int, frozenset] = {}


@dataclass
class DistanceCheck:
    expected: int
    verified: bool
    optimal_length: Optional[int] = None
    solution: Optional[list] = None
    solution_replay_solves: Optional[bool] = None
    independent_lower_bound_holds: Optional[bool] = None
    methods: list = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _independent_ball(depth: int) -> frozenset:
    """Facelet strings of every state reachable in at most ``depth`` moves, built only with pycuber."""
    if depth not in _ball_cache:
        states = set()
        for k in range(depth + 1):
            for seq in itertools.product(MOVES, repeat=k):
                states.add(CubeEngine.solved().apply_moves(seq).facelets)
        _ball_cache[depth] = frozenset(states)
    return _ball_cache[depth]


def verify_exact_distance(facelets: str, expected: int = FINAL_DISTANCE) -> DistanceCheck:
    result = find_optimal(facelets)
    check = DistanceCheck(expected=expected, verified=False, optimal_length=result.length if result.exact else None,
                          solution=result.moves, methods=["bidirectional-bfs (fast engine)"])
    if not (result.exact and result.length == expected and result.moves is not None):
        check.note = (f"Shortest solution is {result.length} moves, not {expected}." if result.exact
                      else f"Not provably {expected} moves away (shortest solution is longer than 8 or unknown).")
        return check
    replay = CubeEngine.from_facelets(facelets).apply_moves(result.moves)
    check.solution_replay_solves = replay.is_solved() and len(result.moves) == expected
    check.methods.append("solution replayed on pycuber engine")
    if expected <= 3:
        check.independent_lower_bound_holds = facelets not in _independent_ball(expected - 1)
        check.methods.append(f"exhaustive pycuber enumeration of all sequences of <= {expected - 1} moves")
        ok_lower = check.independent_lower_bound_holds is True
    else:
        ok_lower = True
    check.verified = bool(check.solution_replay_solves and ok_lower)
    check.note = (f"Proven exactly {expected} moves from solved." if check.verified
                  else "Independent checks disagree; start state rejected.")
    return check


def generate_final_scramble(rng: Optional[random.Random] = None, distance: int = FINAL_DISTANCE) -> list[str]:
    """Quarter-turn scramble whose shortest solution is PROVEN to be exactly ``distance`` moves."""
    rng = rng or random.Random()
    for _ in range(20000):
        moves: list[str] = []
        while len(moves) < distance:
            face = rng.choice([f for f in "URFDLB" if not moves or f != moves[-1][0]])
            moves.append(face + rng.choice(["", "'"]))
        facelets = CubeEngine.from_scramble(moves).facelets
        quick = find_optimal(facelets)
        if quick.exact and quick.length == distance and verify_exact_distance(facelets, distance).verified:
            return moves
    raise RuntimeError("Could not generate a verified scramble")  # pragma: no cover

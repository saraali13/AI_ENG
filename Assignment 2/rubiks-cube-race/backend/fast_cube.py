"""Fast facelet-permutation version of the 18 moves, derived FROM pycuber.

pycuber is accurate but slow (it rebuilds cubie objects on every turn), which is too slow for the
exact shortest-solution search in ``optimal.py`` that visits tens of thousands of states.

Instead of typing 18 permutation tables by hand (easy to get wrong), we ask pycuber itself:
for each move we apply it to a solved pycuber cube and record where every sticker ended up.
``tests/test_fast_cube.py`` then checks this engine against pycuber on thousands of random move sequences.

A state here is a ``bytes`` object of 54 facelet letters (same order as ``CubeEngine.facelets``).
"""
from __future__ import annotations

from operator import itemgetter

import pycuber as pc

from .cube_engine import FACELET_COUNT, MOVES, SOLVED_FACELETS, sticker_identities


def _derive_permutations() -> dict[str, tuple[int, ...]]:
    solved_ids = sticker_identities(pc.Cube())
    index_of = {identity: i for i, identity in enumerate(solved_ids)}
    if len(index_of) != FACELET_COUNT:  # would mean stickers are not uniquely identifiable
        raise RuntimeError("Could not uniquely identify the 54 stickers of the solved cube")
    perms: dict[str, tuple[int, ...]] = {}
    for move in MOVES:
        cube = pc.Cube()
        cube(move)
        # new_state[i] = old_state[perm[i]]
        perms[move] = tuple(index_of[identity] for identity in sticker_identities(cube))
    return perms


PERMUTATIONS: dict[str, tuple[int, ...]] = _derive_permutations()
_GETTERS = {move: itemgetter(*perm) for move, perm in PERMUTATIONS.items()}

SOLVED_STATE: bytes = SOLVED_FACELETS.encode("ascii")


def to_state(facelets: str) -> bytes:
    return facelets.encode("ascii")


def to_facelets(state: bytes) -> str:
    return state.decode("ascii")


def apply_move(state: bytes, move: str) -> bytes:
    """Apply one canonical move (one of ``MOVES``) to a state."""
    return bytes(_GETTERS[move](state))


def apply_moves(state: bytes, moves) -> bytes:
    for move in moves:
        state = apply_move(state, move)
    return state

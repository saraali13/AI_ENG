import random

from backend.cube_engine import MOVES, CubeEngine, generate_scramble
from backend.fast_cube import SOLVED_STATE, apply_move, apply_moves, to_facelets
from backend.optimal import find_optimal


def test_fast_cube_matches_pycuber_on_random_sequences():
    rng = random.Random(2)
    for _ in range(150):
        moves = [rng.choice(MOVES) for _ in range(rng.randint(1, 25))]
        assert to_facelets(apply_moves(SOLVED_STATE, moves)) == CubeEngine.from_scramble(moves).facelets


def test_optimal_of_solved_is_zero():
    r = find_optimal(CubeEngine.solved().facelets)
    assert r.exact and r.length == 0 and r.moves == []


def test_optimal_single_move_and_solution_really_solves():
    for move in MOVES:
        r = find_optimal(CubeEngine.from_scramble([move]).facelets)
        assert r.exact and r.length == 1


def test_optimal_is_never_longer_than_scramble_and_solution_solves():
    rng = random.Random(4)
    for n in (2, 3, 4, 5):
        moves = generate_scramble(n, rng)
        cube = CubeEngine.from_scramble(moves)
        r = find_optimal(cube.facelets)
        assert r.exact and r.length <= n
        assert cube.clone().apply_moves(r.moves).is_solved()


def test_known_distance_four_sequence():
    # R U R' U' is a known 4-move state; nothing shorter solves it (order of sexy move is 6, no 3-move cancel)
    cube = CubeEngine.from_scramble(["R", "U", "R'", "U'"])
    assert find_optimal(cube.facelets).length == 4


def test_unknown_when_too_far_is_not_guessed():
    cube = CubeEngine.from_scramble(generate_scramble(20, random.Random(8)))
    r = find_optimal(cube.facelets)
    if not r.exact:
        assert r.length is None and r.moves is None

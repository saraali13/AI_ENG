import random

import pytest

from backend.cube_engine import (
    CATEGORIES, MOVES, SOLVED_FACELETS, CubeEngine, InvalidMoveError, InvalidStateError,
    generate_scramble, generate_scramble_for_category, inverse_move, invert_sequence,
    normalize_move, simplify_sequence, validate_facelets,
)


def test_there_are_exactly_18_distinct_moves():
    assert len(MOVES) == 18 and len(set(MOVES)) == 18


def test_solved_cube():
    cube = CubeEngine.solved()
    assert cube.is_solved() and cube.facelets == SOLVED_FACELETS and cube.history == []


@pytest.mark.parametrize("move", MOVES)
def test_every_move_changes_the_cube_and_inverse_restores_it(move):
    cube = CubeEngine.solved()
    cube.apply_move(move)
    assert not cube.is_solved()
    cube.apply_move(inverse_move(move))
    assert cube.is_solved()


@pytest.mark.parametrize("face", "URFDLB")
def test_four_quarter_turns_is_identity_and_half_turn_is_two_quarters(face):
    cube = CubeEngine.solved().apply_moves([face] * 4)
    assert cube.is_solved()
    a = CubeEngine.solved().apply_moves([face, face])
    b = CubeEngine.solved().apply_moves([face + "2"])
    assert a.facelets == b.facelets


def test_sequence_then_inverse_sequence_is_solved():
    moves = generate_scramble(18, random.Random(1))
    cube = CubeEngine.from_scramble(moves).apply_moves(invert_sequence(moves))
    assert cube.is_solved()


def test_known_sexy_move_has_order_six():
    cube = CubeEngine.solved()
    for _ in range(6):
        cube.apply_moves(["R", "U", "R'", "U'"])
    assert cube.is_solved()


@pytest.mark.parametrize("bad", ["", "X", "R3", "r", "Rw", "M", "x", "RR", "R 2", "U2'2", None, 5, "turn"])
def test_invalid_moves_are_rejected_and_change_nothing(bad):
    cube = CubeEngine.solved()
    with pytest.raises(InvalidMoveError):
        cube.apply_move(bad)
    assert cube.is_solved() and cube.history == []


def test_apply_moves_is_all_or_nothing():
    cube = CubeEngine.solved()
    with pytest.raises(InvalidMoveError):
        cube.apply_moves(["R", "U", "Q"])
    assert cube.is_solved() and cube.history == []


def test_equivalent_spellings_are_normalised():
    assert normalize_move(" R2' ") == "R2"
    assert normalize_move("R’") == "R'"


def test_history_tracks_moves_and_reset_clears_everything():
    cube = CubeEngine.solved().apply_moves(["R", "U2", "F'"])
    assert cube.history == ["R", "U2", "F'"] and cube.move_count == 3
    cube.reset()
    assert cube.is_solved() and cube.history == []


def test_serialisation_round_trip():
    cube = CubeEngine.from_scramble(["R", "U", "F2", "L'"])
    again = CubeEngine.from_dict(cube.to_dict())
    assert again.facelets == cube.facelets and again.history == cube.history
    assert len(cube.facelets) == 54


@pytest.mark.parametrize("bad", [
    "", "U" * 54, "x" * 54, SOLVED_FACELETS[:-1], SOLVED_FACELETS.replace("UUU", "URU", 1),
])
def test_bad_facelet_strings_are_rejected(bad):
    with pytest.raises(InvalidStateError):
        CubeEngine.from_facelets(bad)


def test_unreachable_state_is_rejected():
    # swap two edge stickers of a single edge: a flipped edge cannot occur on a real cube
    s = list(SOLVED_FACELETS)
    s[7], s[19] = s[19], s[7]  # U-F edge: U bottom-middle <-> F top-middle
    with pytest.raises(InvalidStateError):
        CubeEngine.from_facelets("".join(s))


def test_validate_facelets_accepts_solved():
    assert validate_facelets(SOLVED_FACELETS) == SOLVED_FACELETS


def test_clone_is_independent_of_the_original():
    original = CubeEngine.from_scramble(["R", "U", "F"])
    before = original.facelets
    clone = original.clone()
    assert clone.facelets == before and clone is not original
    clone.apply_move("D")
    assert original.facelets == before and original.history == ["R", "U", "F"]
    original.apply_move("L")
    assert clone.history == ["R", "U", "F", "D"]


def test_simplify_sequence():
    assert simplify_sequence(["R", "R"]) == ["R2"]
    assert simplify_sequence(["R", "R'"]) == []
    assert simplify_sequence(["R", "U", "U'", "R"]) == ["R2"]


# ---------------------------------------------------------------- scrambles
@pytest.mark.parametrize("category", list(CATEGORIES))
def test_category_scramble_lengths(category):
    low, high = CATEGORIES[category]
    rng = random.Random(3)
    for _ in range(60):
        moves = generate_scramble_for_category(category, rng)
        assert low <= len(moves) <= high
        assert all(m in MOVES for m in moves)


def test_assignment_ranges():
    assert CATEGORIES == {"warmup": (3, 4), "primary": (4, 5), "extension": (15, 20), "final": (3, 3)}


def test_scrambles_are_never_solved_and_have_no_redundant_turns():
    rng = random.Random(11)
    for length in list(range(1, 21)) * 15:
        moves = generate_scramble(length, rng)
        assert len(moves) == length
        assert not CubeEngine.from_scramble(moves).is_solved()
        assert all(a[0] != b[0] for a, b in zip(moves, moves[1:]))


def test_same_seed_same_scramble_and_different_seed_differs():
    a = generate_scramble(15, random.Random(5))
    assert a == generate_scramble(15, random.Random(5))
    assert a != generate_scramble(15, random.Random(6))


def test_length_outside_category_is_rejected():
    with pytest.raises(ValueError):
        generate_scramble_for_category("warmup", length=10)
    with pytest.raises(ValueError):
        generate_scramble_for_category("nope")
    with pytest.raises(ValueError):
        generate_scramble(0)


def test_cross_check_against_kociemba_when_installed():
    kociemba = pytest.importorskip("kociemba")
    rng = random.Random(9)
    for _ in range(5):
        cube = CubeEngine.from_scramble(generate_scramble(15, rng))
        solution = kociemba.solve(cube.facelets).split()
        assert cube.clone().apply_moves(solution).is_solved()

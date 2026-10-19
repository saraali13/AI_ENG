import pytest

from backend.models.parsing import extract_move_candidate


@pytest.mark.parametrize("text,expected", [
    ("R", "R"), ('{"move": "U\'"}', "U'"), ("```json\n{\"move\": \"F2\"}\n```", "F2"),
    ("The best move is R'.", "R'"), ('"B"', "B"), ("move: D2", "D2"), ("U3", "U3"),
])
def test_extracts_candidates(text, expected):
    assert extract_move_candidate(text) == expected


@pytest.mark.parametrize("text", ["", "   ", None, 5, "I think the cube is hard to solve today honestly"])
def test_returns_none_when_no_candidate(text):
    assert extract_move_candidate(text) is None

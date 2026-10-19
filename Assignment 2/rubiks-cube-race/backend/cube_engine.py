"""3x3 Rubik's Cube engine built on the proven ``pycuber`` library.

Every cube state here is a real ``pycuber.Cube``; moves are applied by pycuber itself.

Conventions
-----------
* Faces: U(p), R(ight), F(ront), D(own), L(eft), B(ack).
* The 18 standard face-turn moves are each of the six faces with a suffix:
  ``X`` = 90 degrees clockwise, ``X'`` = 90 degrees counter-clockwise, ``X2`` = 180 degrees.
* Counting convention (face-turn metric): every one of the 18 moves counts as ONE move,
  so ``R2`` is one move, not two.
* A cube state is serialised as a 54-character "facelet string" in the standard
  Kociemba order: U face, R face, F face, D face, L face, B face, each read
  row by row (top-left to bottom-right) in the usual unfolded-net orientation.
  Each character names the face whose centre has that sticker's colour, so the
  solved cube is ``UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB``.
"""
from __future__ import annotations

import random
import re
from typing import Iterable, Optional, Sequence

import pycuber as pc

FACES = "URFDLB"
SUFFIXES = ("", "'", "2")
MOVES: tuple[str, ...] = tuple(face + suffix for face in FACES for suffix in SUFFIXES)
MOVE_SET = frozenset(MOVES)
OPPOSITE_FACE = {"U": "D", "D": "U", "R": "L", "L": "R", "F": "B", "B": "F"}

SOLVED_FACELETS = "".join(face * 9 for face in FACES)
FACELET_COUNT = 54
CENTER_INDEXES = {face: i * 9 + 4 for i, face in enumerate(FACES)}

# pycuber's default colour scheme (read off the library, verified in tests).
_FACE_TO_COLOUR = {"U": "yellow", "R": "orange", "F": "green", "D": "white", "L": "red", "B": "blue"}
_COLOUR_TO_FACE = {colour: face for face, colour in _FACE_TO_COLOUR.items()}

# Scramble categories from the assignment: inclusive (min, max) number of scramble moves.
CATEGORIES: dict[str, tuple[int, int]] = {
    "warmup": (3, 4),
    "primary": (4, 5),
    "extension": (15, 20),
    # The professor's final experiment: EXACTLY 3 moves from solved, proven by a shortest-path check (see experiment.py).
    "final": (3, 3),
}

COUNTING_CONVENTION = (
    "Face-turn metric: each of the 18 moves (U, U', U2, R, R', R2, F, F', F2, D, D', D2, L, L', L2, B, B', B2) "
    "counts as exactly one move. The scramble length and every competitor's move count use this convention. "
    "The move limit counts valid moves that were actually applied to the cube."
)


class InvalidMoveError(ValueError):
    """Raised when a move string is not one of the 18 legal face turns."""


class InvalidStateError(ValueError):
    """Raised when a facelet string cannot describe a cube."""


# --------------------------------------------------------------------------- moves

_PRIME_VARIANTS = str.maketrans({"’": "'", "‘": "'", "′": "'", "ʹ": "'"})


def normalize_move(move: str) -> str:
    """Return the canonical spelling of ``move`` or raise ``InvalidMoveError``.

    Accepts the 18 canonical moves, typographic apostrophes, and the equivalent spellings
    ``R2'`` / ``R'2`` for ``R2``. Everything else (lowercase wide moves, slice moves,
    rotations, free text, ...) is rejected rather than guessed.
    """
    if not isinstance(move, str):
        raise InvalidMoveError(f"Move must be a string, got {type(move).__name__}")
    text = move.strip().translate(_PRIME_VARIANTS)
    if text in MOVE_SET:
        return text
    if len(text) == 3 and text[0] in FACES and set(text[1:]) == {"2", "'"}:
        return text[0] + "2"
    shown = text if len(text) <= 40 else text[:40] + "..."
    raise InvalidMoveError(f"Illegal move {shown!r}: expected one of {', '.join(MOVES)}")


validate_move = normalize_move


def is_valid_move(move: object) -> bool:
    try:
        normalize_move(move)  # type: ignore[arg-type]
    except InvalidMoveError:
        return False
    return True


def inverse_move(move: str) -> str:
    move = normalize_move(move)
    if move.endswith("2"):
        return move
    if move.endswith("'"):
        return move[0]
    return move + "'"


def invert_sequence(moves: Iterable[str]) -> list[str]:
    """The sequence that undoes ``moves`` (reversed, each move inverted)."""
    return [inverse_move(m) for m in reversed(list(moves))]


def parse_sequence(text: str) -> list[str]:
    """Parse ``"R U R' U2"`` into a validated list of moves."""
    return [normalize_move(token) for token in text.split()]


def format_sequence(moves: Iterable[str]) -> str:
    return " ".join(moves)


_QUARTERS = {"": 1, "2": 2, "'": 3}
_QUARTER_TO_SUFFIX = {1: "", 2: "2", 3: "'"}


def simplify_sequence(moves: Iterable[str]) -> list[str]:
    """Merge/cancel consecutive turns of the same face (``R R`` -> ``R2``, ``R R'`` -> nothing)."""
    stack: list[tuple[str, int]] = []
    for move in moves:
        move = normalize_move(move)
        face, quarters = move[0], _QUARTERS[move[1:]]
        if stack and stack[-1][0] == face:
            total = (stack[-1][1] + quarters) % 4
            stack.pop()
            if total:
                stack.append((face, total))
        else:
            stack.append((face, quarters))
    return [face + _QUARTER_TO_SUFFIX[q] for face, q in stack]


# --------------------------------------------------------------------------- scrambles


def category_range(category: str) -> tuple[int, int]:
    try:
        return CATEGORIES[category]
    except KeyError:
        raise ValueError(f"Unknown category {category!r}; choose one of {', '.join(CATEGORIES)}") from None


def generate_scramble(length: int, rng: Optional[random.Random] = None) -> list[str]:
    """Random scramble of exactly ``length`` moves with no redundant turns.

    Rules: never turn the same face twice in a row, and never turn a face, then its
    opposite, then the first face again (``U D U'`` is just ``U2 D``). This guarantees
    every move counts and that the result is never the solved cube.
    """
    if not isinstance(length, int) or length < 1:
        raise ValueError("Scramble length must be a positive integer")
    rng = rng or random.Random()
    while True:
        moves: list[str] = []
        for _ in range(length):
            allowed = [f for f in FACES if not (moves and f == moves[-1][0])]
            if len(moves) >= 2 and OPPOSITE_FACE[moves[-1][0]] == moves[-2][0]:
                allowed = [f for f in allowed if f != moves[-2][0]]
            face = rng.choice(allowed)
            moves.append(face + rng.choice(SUFFIXES))
        # Safety net: with the rules above this never happens, but never hand out a solved cube.
        if not CubeEngine.solved().apply_moves(moves).is_solved():
            return moves


def generate_scramble_for_category(category: str, rng: Optional[random.Random] = None,
                                   length: Optional[int] = None) -> list[str]:
    low, high = category_range(category)
    rng = rng or random.Random()
    if category == "final":  # never hand out an unverified "3-move" scramble: it must be PROVEN 3 moves away
        if length is not None and length != 3:
            raise ValueError("The final experiment uses exactly 3 moves")
        from .experiment import generate_final_scramble  # local import: experiment.py imports this module
        return generate_final_scramble(rng)
    if length is None:
        length = rng.randint(low, high)
    elif not low <= length <= high:
        raise ValueError(f"Length {length} is outside the {category!r} range {low}-{high}")
    return generate_scramble(length, rng)


# --------------------------------------------------------------------------- facelets


def validate_facelets(facelets: str) -> str:
    """Check that ``facelets`` is a well-formed 54-character cube string (not full solvability)."""
    if not isinstance(facelets, str):
        raise InvalidStateError("Facelets must be a string")
    text = facelets.strip().upper()
    if len(text) != FACELET_COUNT:
        raise InvalidStateError(f"Facelet string must have {FACELET_COUNT} characters, got {len(text)}")
    bad = sorted(set(text) - set(FACES))
    if bad:
        raise InvalidStateError(f"Facelet string may only contain {', '.join(FACES)}; found {', '.join(bad)}")
    for face in FACES:
        count = text.count(face)
        if count != 9:
            raise InvalidStateError(f"Each colour must appear exactly 9 times; {face} appears {count} times")
    for face, index in CENTER_INDEXES.items():
        if text[index] != face:
            raise InvalidStateError(f"The centre of face {face} must be {face}, got {text[index]}")
    return text




def _read_facelets(cube: "pc.Cube") -> str:
    return "".join(
        _COLOUR_TO_FACE[square.colour] for face in FACES for row in cube.get_face(face) for square in row
    )


def sticker_identities(cube: "pc.Cube") -> list[tuple[frozenset, str]]:
    """Identify every sticker of a cube in facelet order.

    A sticker is identified by (set of colours on its cubie, its own colour). On a physically valid
    cube that pair is unique, so it lets us follow individual stickers through a move.
    """
    owner = {id(sq): cubie for cubie in cube.children for sq in cubie.facings.values()}
    identities = []
    for face in FACES:
        for row in cube.get_face(face):
            for square in row:
                cubie = owner[id(square)]
                identities.append((frozenset(s.colour for s in cubie.facings.values()), square.colour))
    return identities


def _cubie_sticker_layout() -> dict[frozenset, list[tuple[str, int]]]:
    """For every cubie (keyed by the set of faces it touches): its (face, facelet index) stickers."""
    cube = pc.Cube()
    owner = {id(sq): cubie for cubie in cube.children for sq in cubie.facings.values()}
    layout: dict[frozenset, list[tuple[str, int]]] = {}
    index = 0
    for face in FACES:
        for row in cube.get_face(face):
            for square in row:
                layout.setdefault(frozenset(owner[id(square)].facings), []).append((face, index))
                index += 1
    return layout


_CUBIE_LAYOUT = _cubie_sticker_layout()
_CUBIE_CLASS = {1: pc.Centre, 2: pc.Edge, 3: pc.Corner}


def _build_cube(facelets: str) -> "pc.Cube":
    """Build a brand-new ``pycuber.Cube`` (fresh Square objects) showing ``facelets``.

    Cubies are constructed rather than edited in place: pycuber keeps cubies in a set keyed by their
    colours, so mutating squares of an existing cube corrupts it.
    """
    cubies = set()
    for entries in _CUBIE_LAYOUT.values():
        stickers = {face: pc.Square(_FACE_TO_COLOUR[facelets[index]]) for face, index in entries}
        cubies.add(_CUBIE_CLASS[len(entries)](**stickers))
    cube = pc.Cube(cubies)
    if len(cube.children) != 26:
        raise InvalidStateError("Facelet string describes duplicate or missing pieces, so it is not a real cube")
    try:
        solvable = cube.is_valid()
    except Exception:  # pycuber's checker can raise on nonsense input
        solvable = False
    if not solvable:
        raise InvalidStateError(
            "Facelet string is not a reachable cube state (twisted corner, flipped edge or parity error)"
        )
    return cube


class CubeEngine:
    """A 3x3 cube with move history, backed by ``pycuber.Cube``."""

    def __init__(self, facelets: Optional[str] = None, history: Optional[Sequence[str]] = None):
        self._cube = pc.Cube()
        self.history: list[str] = [normalize_move(m) for m in history] if history else []
        if facelets is not None:
            self._load_facelets(facelets)

    # ---- construction ----------------------------------------------------
    @classmethod
    def solved(cls) -> "CubeEngine":
        """Create a solved cube."""
        return cls()

    @classmethod
    def from_facelets(cls, facelets: str, history: Optional[Sequence[str]] = None) -> "CubeEngine":
        return cls(facelets, history)

    @classmethod
    def from_dict(cls, data: dict) -> "CubeEngine":
        return cls(data["facelets"], data.get("history") or [])

    @classmethod
    def from_scramble(cls, moves: Iterable[str]) -> "CubeEngine":
        """A solved cube with ``moves`` applied. The scramble itself is kept as the history."""
        return cls.solved().apply_moves(moves)

    def _load_facelets(self, facelets: str) -> None:
        self._cube = _build_cube(validate_facelets(facelets))

    # ---- state -------------------------------------------------------------
    @property
    def facelets(self) -> str:
        return _read_facelets(self._cube)

    def is_solved(self) -> bool:
        """True when every face shows a single colour (centres never move under face turns)."""
        return self.facelets == SOLVED_FACELETS

    @property
    def move_count(self) -> int:
        return len(self.history)

    def faces(self) -> dict[str, list[str]]:
        """Each face as three 3-character rows, e.g. ``{"U": ["UUU", "UUU", "UUU"], ...}``."""
        s = self.facelets
        return {face: [s[i * 9 + r * 3: i * 9 + r * 3 + 3] for r in range(3)] for i, face in enumerate(FACES)}

    def net(self) -> str:
        """Plain-text unfolded net (U on top; L F R B in the middle; D at the bottom)."""
        f = self.faces()
        pad = " " * 4
        lines = [pad + " ".join(row) for row in f["U"]]
        for r in range(3):
            lines.append("  ".join(" ".join(f[face][r]) for face in "LFRB"))
        lines += [pad + " ".join(row) for row in f["D"]]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "facelets": self.facelets,
            "history": list(self.history),
            "move_count": self.move_count,
            "solved": self.is_solved(),
        }

    # ---- operations ----------------------------------------------------------
    def apply_move(self, move: str) -> str:
        """Validate and apply one move; returns the canonical move. Illegal moves raise and change nothing."""
        canonical = normalize_move(move)
        self._cube(canonical)
        self.history.append(canonical)
        return canonical

    def apply_moves(self, moves: Iterable[str]) -> "CubeEngine":
        """Apply several moves. All moves are validated first, so a bad move changes nothing."""
        canonical = [normalize_move(m) for m in moves]
        for move in canonical:
            self._cube(move)
            self.history.append(move)
        return self

    def clone(self) -> "CubeEngine":
        """Independent deep copy (state and history); changing the clone never affects the original."""
        other = CubeEngine.__new__(CubeEngine)
        other._cube = _build_cube(self.facelets)  # fresh pieces, nothing shared with the original
        other.history = list(self.history)
        return other

    def reset(self) -> "CubeEngine":
        """Back to a solved cube with an empty history."""
        self._cube = pc.Cube()
        self.history = []
        return self

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CubeEngine) and self.facelets == other.facelets

    def __repr__(self) -> str:
        return f"CubeEngine({self.facelets!r}, moves={self.move_count})"

    __hash__ = None  # type: ignore[assignment]  (mutable)

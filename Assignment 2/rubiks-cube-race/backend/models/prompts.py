"""What the real models are shown. Identical information for every competitor.

Models see ONLY the current cube state, the notation, and the moves they have already made.
They are never told the scramble, so nobody can "cheat" by inverting it.
"""
from __future__ import annotations

import json

from ..cube_engine import FACES, MOVES
from .base import StepContext

_FACE_NAMES = {"U": "Up (top)", "R": "Right", "F": "Front", "D": "Down (bottom)", "L": "Left", "B": "Back"}

MOVE_DESCRIPTIONS: dict[str, str] = {}
for _face in FACES:
    MOVE_DESCRIPTIONS[_face] = f"Turn the {_FACE_NAMES[_face]} face 90 degrees clockwise (looking at that face)"
    MOVE_DESCRIPTIONS[_face + "'"] = (
        f"Turn the {_FACE_NAMES[_face]} face 90 degrees counter-clockwise (looking at that face)"
    )
    MOVE_DESCRIPTIONS[_face + "2"] = f"Turn the {_FACE_NAMES[_face]} face 180 degrees"
assert set(MOVE_DESCRIPTIONS) == set(MOVES)

FACELET_HELP = (
    "The cube is given as 54 stickers in this face order: U, R, F, D, L, B. Each face is read row by row, "
    "top-left to bottom-right, in the standard unfolded net (U on top; L, F, R, B in a row beneath it; D at the "
    "bottom; looking at each face from outside the cube, with U's top edge next to B). Each letter is the face "
    "whose CENTRE has that sticker's colour. The solved cube is nine U, nine R, nine F, nine D, nine L, nine B."
)
NOTATION_HELP = (
    "Moves: X = clockwise quarter turn of face X (viewed from outside), X' = counter-clockwise quarter turn, "
    "X2 = half turn, for X in U R F D L B. These 18 moves are the only legal moves."
)

TASK = (
    "Solve this 3x3 Rubik's Cube. Pick the single best next move; moves are applied one at a time and the new "
    "state is shown again."
)


def state_payload(ctx: StepContext) -> dict:
    """Structured description of the current cube state (JSON-serialisable)."""
    return {
        "task": TASK,
        "facelets": ctx.facelets,
        "faces": ctx.faces,
        "facelet_help": FACELET_HELP,
        "notation": NOTATION_HELP,
        "moves_made_so_far": list(ctx.history),
        "moves_remaining_before_limit": ctx.moves_remaining,
        "previous_reply_problem": ctx.last_error,
    }


def decisions_question() -> dict:
    """The single ``choice`` question sent to a Decisions-API model: which of the 18 moves comes next."""
    return {
        "next_move": {
            "type": "choice",
            "instructions": (
                "Which single face turn should be applied next so the cube gets closer to solved? "
                "Choose exactly one move."
            ),
            "criteria": {move: MOVE_DESCRIPTIONS[move] for move in MOVES},
        }
    }


CHAT_SYSTEM_PROMPT = (
    "You are an expert Rubik's Cube solver. You solve a 3x3 cube one move at a time. "
    + NOTATION_HELP
    + " "
    + FACELET_HELP
    + ' Reply with ONLY a JSON object of the form {"move": "R\'"} containing exactly one of the 18 legal moves, '
    "and nothing else."
)


def chat_user_prompt(ctx: StepContext) -> str:
    return (
        "Current cube state:\n"
        + json.dumps(state_payload(ctx), separators=(",", ":"))
        + "\n\nChoose the single best next move. Respond with only JSON like {\"move\": \"U2\"}."
    )

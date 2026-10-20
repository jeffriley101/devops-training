"""Small, deterministic issued Homework catalog; no completion or reward writer."""
from datetime import date

from .content import GOAL_OPTIONS
from .instruments import INSTRUMENT_DEFINITIONS, canonical_instrument_key


SCHEMA_VERSION = "weekly_homework_v1"
RECIPES = {
    "piano-keyboard": ("a scale or chord progression with both hands",
                       "alternating scale and chord progression practice with both hands"),
    "flute": ("a trill", "a trill at metronome speeds"),
    "clarinet": ("crossing the break", "repeated cross-the-break work at metronome speeds"),
    "saxophone": ("middle-B tonguing", "tonguing at different speeds"),
    "trumpet": ("low-F# tonguing", "tonguing at different speeds"),
    "trombone": ("first-position lip slurs", "lip slurs with a metronome"),
    "tuba": ("sustaining an easy note three times", "tuner consistency on an easy note"),
    "percussion": ("ten paradiddles", "even paradiddles with a metronome"),
}


def weekly_instrument(value):
    """Existing aliases are accepted; retired-only and unknown choices cannot start work."""
    key = canonical_instrument_key(value)
    if key not in {item["key"] for item in INSTRUMENT_DEFINITIONS}:
        raise ValueError("Choose a currently supported instrument before starting weekly work.")
    return key


def homework_spec(plan, instrument):
    key = weekly_instrument(instrument)
    if plan.focus not in GOAL_OPTIONS:
        raise ValueError("Unknown issued Focus.")
    rotation = ((plan.week_start - date(1970, 1, 5)).days // 7) % 2
    choices = RECIPES.get(key)
    exercise = choices[rotation] if choices else "a difficult passage slowly and evenly"
    recipe = f"{key}:{'ab'[rotation] if choices else 'passage'}"
    percussion = key == "percussion"
    if plan.focus == "Build daily consistency":
        title = "Short, repeatable practice"
        steps = [f"Choose {exercise}.", "Schedule short, repeatable practice blocks.",
                 "Return to the same exercise in each block."]
    elif plan.focus == "Improve tone and control":
        title = "Steady stroke and pulse" if percussion else "Steady tone and control"
        steps = [f"Work on {exercise}.",
                 "Listen for steady stroke and pulse." if percussion else "Listen for steady tone and control.",
                 "Repeat slowly and evenly, keeping the sound consistent."]
    elif plan.focus == "Learn songs faster":
        title = "Build a passage in short segments"
        steps = ["Choose a difficult passage on this instrument and divide it into short segments.",
                 "Practice each segment slowly and evenly.", "Join the segments in a continuous attempt."]
    else:
        title = "Vary a rhythm phrase" if percussion else "Vary a comfortable-note phrase"
        steps = ["Choose a short rhythm phrase." if percussion else "Choose a short phrase using comfortable notes.",
                 "Repeat the phrase, then vary its rhythm.", "Connect the variations in a continuous phrase."]

    # Only these bounded, server-owned counts affect support. No notes, rating,
    # current preference or chart quantity is read by the generator.
    logging_days = sum(week["logging_days"] for week in plan.recent_results["weeks"][:2])
    shorter = logging_days < 4
    mode = plan.issued_game_mode
    if mode == "Beginner":
        presentation, blocks = "numbered-steps", 5
        steps.append("Follow these steps in order, one at a time.")
    elif mode == "Challenge":
        presentation, blocks = "repetition-metronome", 5 if shorter else 3
        steps.append("Repeat with a metronome at a comfortable speed, keeping each attempt even.")
    elif mode == "Performance":
        presentation, blocks = "continuous-attempt", 4 if shorter else 2
        steps.append("Finish each block with a continuous attempt; notice where support would help.")
    else:
        raise ValueError("Unknown issued Game Mode.")
    if plan.homework_minutes_target <= 0 or plan.homework_minutes_target % blocks:
        raise ValueError("Invalid issued Homework duration.")
    return {"schema_version": SCHEMA_VERSION, "recipe_id": recipe, "title": title,
            "instructions": steps, "presentation": presentation,
            "suggested_blocks": {"count": blocks, "minutes_each": plan.homework_minutes_target // blocks},
            "support": "shorter-blocks" if shorter else "ordinary-blocks"}

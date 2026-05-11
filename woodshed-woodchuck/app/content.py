"""Static content used by the Woodshed Woodchuck demo."""

INSTRUMENT_OPTIONS = [
    "Saxophone",
    "Guitar",
    "Piano",
    "Voice",
    "Trumpet",
    "Violin",
]

LEVEL_OPTIONS = ["Beginner", "Intermediate", "Advanced"]

GOAL_OPTIONS = [
    "Build daily consistency",
    "Improve tone and control",
    "Learn songs faster",
    "Strengthen improvisation",
]

SAX_VIKING_WELCOME = "Ahoy, musician! Set your path and let the woodshed sing."

QUEST_POOL = {
    "Saxophone": [
        {"id": "sax-long-tone", "text": "Play long tones across your comfortable range.", "target_minutes": 15, "reward_credits": 20},
        {"id": "sax-scale", "text": "Play a B-flat major scale with a metronome.", "target_minutes": 20, "reward_credits": 25},
    ],
    "Guitar": [
        {"id": "gtr-chords", "text": "Practice clean chord changes through 4 shapes.", "target_minutes": 15, "reward_credits": 20},
        {"id": "gtr-scale", "text": "Run a major scale in two positions.", "target_minutes": 20, "reward_credits": 25},
    ],
    "Piano": [
        {"id": "pn-hands", "text": "Play a major scale hands-separate then hands-together.", "target_minutes": 20, "reward_credits": 25},
        {"id": "pn-rhythm", "text": "Practice a chord progression with steady rhythm.", "target_minutes": 15, "reward_credits": 20},
    ],
    "Voice": [
        {"id": "vox-breath", "text": "Do breath control and sustained vowel practice.", "target_minutes": 15, "reward_credits": 20},
        {"id": "vox-intervals", "text": "Practice interval jumps with a reference pitch.", "target_minutes": 20, "reward_credits": 25},
    ],
    "Trumpet": [
        {"id": "trp-long-tone", "text": "Play long tones with steady embouchure.", "target_minutes": 15, "reward_credits": 20},
        {"id": "trp-articulation", "text": "Practice tonguing patterns at two tempos.", "target_minutes": 20, "reward_credits": 25},
    ],
    "Violin": [
        {"id": "vln-bow", "text": "Work on even bow strokes across open strings.", "target_minutes": 15, "reward_credits": 20},
        {"id": "vln-scale", "text": "Play a two-octave scale with intonation focus.", "target_minutes": 20, "reward_credits": 25},
    ],
}

SAX_VIKING_MESSAGES = {
    "reward": [
        "Legendary effort. Your woodshed spirit grows stronger!",
        "Now that is honest work, musician. Keep the fire alive.",
        "You met the mark today. The Woodchuck is grinning ear to ear.",
    ],
    "supportive": [
        "Good start. Keep going until you hit today's target.",
        "Every minute counts. You've begun the climb.",
        "Solid progress—finish the target to claim today's reward.",
    ],
    "already_done": [
        "You've already conquered today's quest. Rest those chops.",
    ],
}

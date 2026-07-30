"""Static content used by the Woodshed Woodchuck demo."""

from .instruments import INSTRUMENT_OPTIONS

LEVEL_OPTIONS = [
    "Beginner", "Intermediate", "Advanced", "High School", "Honors",
    "College", "Weekend Warrior", "Professional", "Legend",
    "Mount Rushmore",
]

GOAL_OPTIONS = [
    "Build daily consistency",
    "Improve tone and control",
    "Learn songs faster",
    "Strengthen improvisation",
]

SAX_VIKING_WELCOME = "Ahoy, musician! Set your path and let the woodshed sing."

QUEST_POOL = {
    "Flute": [
        {
            "id": "flute-trill",
            "text": "Practice a trill.",
            "target_minutes": 10,
            "reward_credits": 15,
        },
        {
            "id": "flute-trill-metronome",
            "text": "Use the metronome to practice trilling at different speeds.",
            "target_minutes": 15,
            "reward_credits": 20,
        },
    ],
    "Clarinet": [
        {
            "id": "clarinet-break",
            "text": "Practice playing over the break.",
            "target_minutes": 10,
            "reward_credits": 15,
        },
        {
            "id": "clarinet-break-metronome",
            "text": "Use the metronome to practice playing over the break at different speeds.",
            "target_minutes": 15,
            "reward_credits": 20,
        },
    ],
    "Saxophone": [
        {
            "id": "sax-middle-b-tonguing",
            "text": "Play a middle B and practice tonguing.",
            "target_minutes": 10,
            "reward_credits": 15,
        },
        {
            "id": "sax-tonguing-metronome",
            "text": "Use the metronome to practice tonguing at different speeds.",
            "target_minutes": 15,
            "reward_credits": 20,
        },
    ],
    "Trumpet": [
        {
            "id": "trumpet-low-f-sharp-tonguing",
            "text": "Play a low F# and practice tonguing.",
            "target_minutes": 10,
            "reward_credits": 15,
        },
        {
            "id": "trumpet-tonguing-metronome",
            "text": "Use the metronome to practice tonguing at different speeds.",
            "target_minutes": 15,
            "reward_credits": 20,
        },
    ],
    "Trombone": [
        {
            "id": "trombone-first-position-lip-slurs",
            "text": "Play in the first position and practice lip slurs.",
            "target_minutes": 10,
            "reward_credits": 15,
        },
        {
            "id": "trombone-lip-slurs-metronome",
            "text": "Use the metronome to practice lip slurs at different speeds.",
            "target_minutes": 15,
            "reward_credits": 20,
        },
    ],
    "Tuba": [
        {
            "id": "tuba-long-note",
            "text": "Play an easy note for as long as you can, three times.",
            "target_minutes": 10,
            "reward_credits": 15,
        },
        {
            "id": "tuba-tuner-pitch",
            "text": "Use the tuner to make sure your pitch stays consistent.",
            "target_minutes": 15,
            "reward_credits": 20,
        },
    ],
    "Percussion": [
        {
            "id": "percussion-paradiddles",
            "text": "Play 10 paradiddles.",
            "target_minutes": 10,
            "reward_credits": 15,
        },
        {
            "id": "percussion-paradiddles-metronome",
            "text": "Use the metronome to make sure your paradiddles are even at different speeds.",
            "target_minutes": 15,
            "reward_credits": 20,
        },
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
        "Solid progress — finish the target to claim today's reward.",
    ],
    "already_done": [
        "You've already conquered today's quest. Rest those chops.",
        "The quest is complete. Extra practice still feeds the woodshed.",
    ],
}

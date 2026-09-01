from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo


HISTORY_MYSTERY_TIMEZONE = ZoneInfo("America/Chicago")
HISTORY_MYSTERY_GAME_KEY = "history-mystery"
HISTORY_MYSTERY_BANK_VERSION = "v1"
HISTORY_MYSTERY_CATEGORIES = (
    "WHO AM I?",
    "WHO CHANGED ME?",
    "BIG YEAR",
    "HISTORY MYSTERY",
    "FAMOUS FACE",
)
HISTORY_MYSTERY_INSTRUMENTS = (
    "Flute",
    "Clarinet",
    "Saxophone",
    "Trumpet",
    "Trombone",
    "Tuba",
)


def _question(
    question_id: str,
    category: str,
    prompt: str,
    choices: tuple[str, ...],
    answer: str,
    fact: str,
    instrument: str,
    source_url: str,
) -> dict[str, object]:
    return {
        "id": question_id,
        "category": category,
        "prompt": prompt,
        "choices": choices,
        "answer": answer,
        "fact": fact,
        "instrument": instrument,
        "source_url": source_url,
    }


HISTORY_MYSTERY_QUESTIONS = (
    _question(
        "who-clarinet-reed", "WHO AM I?",
        "I use one reed and often have a black body with silver-colored keys. What am I?",
        ("Clarinet", "Flute", "Trumpet", "Trombone"), "Clarinet",
        "A clarinet makes sound with a single reed attached to its mouthpiece.", "Clarinet",
        "https://www.yamaha.com/en/musical_instrument_guide/clarinet/structure/",
    ),
    _question(
        "who-flute-edge", "WHO AM I?",
        "You blow across my mouth hole, and I do not use a reed. What am I?",
        ("Flute", "Clarinet", "Saxophone", "Tuba"), "Flute",
        "A flute player directs air across the edge of the embouchure hole.", "Flute",
        "https://www.yamaha.com/en/musical_instrument_guide/flute/structure/",
    ),
    _question(
        "who-sax-brass-reed", "WHO AM I?",
        "My body is brass, but my single reed makes me a woodwind. What am I?",
        ("Saxophone", "Trumpet", "Clarinet", "Tuba"), "Saxophone",
        "The saxophone belongs to the woodwind family because its sound begins with a reed.", "Saxophone",
        "https://www.yamaha.com/en/musical_instrument_guide/saxophone/",
    ),
    _question(
        "who-trombone-slide", "WHO AM I?",
        "I change most notes by moving a long slide. What am I?",
        ("Trombone", "Trumpet", "Tuba", "Flute"), "Trombone",
        "The trombone slide changes the length of the air column.", "Trombone",
        "https://www.yamaha.com/en/musical_instrument_guide/trombone/structure/",
    ),
    _question(
        "who-tuba-low-brass", "WHO AM I?",
        "I am the large, low-voiced brass instrument in the band. What am I?",
        ("Tuba", "Trombone", "Trumpet", "Saxophone"), "Tuba",
        "The tuba supplies many of the lowest notes in a concert band.", "Tuba",
        "https://www.yamaha.com/en/musical_instrument_guide/tuba/structure/",
    ),

    _question(
        "changed-flute-boehm", "WHO CHANGED ME?",
        "Who designed the key system and metal flute that became the basis of the modern flute?",
        ("Theobald Boehm", "Adolphe Sax", "Johann Moritz", "Benny Goodman"), "Theobald Boehm",
        "Theobald Boehm presented his revolutionary flute design in 1847.", "Flute",
        "https://www.yamaha.com/en/musical_instrument_guide/flute/structure/",
    ),
    _question(
        "changed-clarinet-denner", "WHO CHANGED ME?",
        "Who is generally credited with developing the clarinet from the chalumeau?",
        ("Johann Christoph Denner", "Theobald Boehm", "Adolphe Sax", "Glenn Miller"), "Johann Christoph Denner",
        "Denner's work around the start of the 1700s helped create the clarinet.", "Clarinet",
        "https://www.yamaha.com/en/musical_instrument_guide/clarinet/structure/",
    ),
    _question(
        "changed-sax-adolphe", "WHO CHANGED ME?",
        "Which Belgian instrument maker invented the saxophone?",
        ("Adolphe Sax", "Theobald Boehm", "Johann Denner", "Harvey Phillips"), "Adolphe Sax",
        "Adolphe Sax created the saxophone family in the 1840s.", "Saxophone",
        "https://www.yamaha.com/en/musical_instrument_guide/saxophone/",
    ),
    _question(
        "changed-tuba-builders", "WHO CHANGED ME?",
        "Who worked together on the valved basstuba patented in 1835?",
        ("Wilhelm Wieprecht and Johann Moritz", "Boehm and Klose", "Sax and Denner", "Davis and Coltrane"),
        "Wilhelm Wieprecht and Johann Moritz",
        "Wieprecht designed the basstuba and Moritz built it.", "Tuba",
        "https://www.yamaha.com/en/musical_instrument_guide/tuba/structure/",
    ),
    _question(
        "changed-clarinet-klose", "WHO CHANGED ME?",
        "Who adapted Boehm-style keywork into the widely used modern clarinet system?",
        ("Hyacinthe Klose", "Miles Davis", "James Galway", "Johann Moritz"), "Hyacinthe Klose",
        "Hyacinthe Klose's 1800s key system became a standard for clarinets.", "Clarinet",
        "https://www.yamaha.com/en/musical_instrument_guide/clarinet/structure/",
    ),

    _question(
        "year-sax-patent-1846", "BIG YEAR",
        "In what year did Adolphe Sax receive his French saxophone patent?",
        ("1846", "1808", "1835", "1901"), "1846",
        "Sax received the French patent for his new instrument in 1846.", "Saxophone",
        "https://www.yamaha.com/en/musical_instrument_guide/saxophone/",
    ),
    _question(
        "year-tuba-patent-1835", "BIG YEAR",
        "In what year was the valved basstuba patented?",
        ("1835", "1847", "1776", "1925"), "1835",
        "The basstuba patent was filed on September 12, 1835.", "Tuba",
        "https://www.yamaha.com/en/musical_instrument_guide/tuba/structure/",
    ),
    _question(
        "year-boehm-flute-1847", "BIG YEAR",
        "In what year did Theobald Boehm present his revolutionary modern flute design?",
        ("1847", "1810", "1865", "1912"), "1847",
        "Boehm demonstrated the new flute at the 1847 Paris Exhibition.", "Flute",
        "https://www.yamaha.com/en/musical_instrument_guide/flute/structure/",
    ),
    _question(
        "year-beethoven-trombone-1808", "BIG YEAR",
        "Beethoven's Fifth, an early symphony to use trombones, premiered in what year?",
        ("1808", "1735", "1846", "1908"), "1808",
        "Beethoven included trombones in his Fifth Symphony, premiered in 1808.", "Trombone",
        "https://www.yamaha.com/en/musical_instrument_guide/trombone/structure/",
    ),
    _question(
        "year-trumpet-valve-1810", "BIG YEAR",
        "Around what year was the first practical brass-instrument valve developed?",
        ("1810", "1660", "1846", "1950"), "1810",
        "The valve appeared around 1810 and let brass players change tubing length quickly.", "Trumpet",
        "https://www.yamaha.com/en/musical_instrument_guide/trumpet/structure/structure002.html",
    ),

    _question(
        "mystery-sax-family", "HISTORY MYSTERY",
        "Why is the metal saxophone placed in the woodwind family?",
        ("It uses a reed", "It has a bell", "It uses valves", "It was first made of wood"), "It uses a reed",
        "Instrument families are based mainly on how sound begins, not just body material.", "Saxophone",
        "https://www.yamaha.com/en/musical_instrument_guide/saxophone/",
    ),
    _question(
        "mystery-trombone-sackbut", "HISTORY MYSTERY",
        "What older name was commonly used for an early trombone?",
        ("Sackbut", "Chalumeau", "Ophicleide", "Cornetto"), "Sackbut",
        "The trombone was called a sackbut in earlier centuries.", "Trombone",
        "https://www.yamaha.com/en/musical_instrument_guide/trombone/structure/",
    ),
    _question(
        "mystery-trumpet-valves", "HISTORY MYSTERY",
        "What do trumpet valves change when a player presses them?",
        ("The tubing length", "The bell material", "The mouthpiece size", "The player's air speed"), "The tubing length",
        "Valves route air through extra tubing, changing the instrument's sounding length.", "Trumpet",
        "https://www.yamaha.com/en/musical_instrument_guide/trumpet/structure/structure002.html",
    ),
    _question(
        "mystery-clarinet-chalumeau", "HISTORY MYSTERY",
        "Which earlier single-reed instrument helped lead to the clarinet?",
        ("Chalumeau", "Sackbut", "Serpent", "Bugle"), "Chalumeau",
        "The chalumeau was an important ancestor of the clarinet.", "Clarinet",
        "https://www.yamaha.com/en/musical_instrument_guide/clarinet/structure/",
    ),
    _question(
        "mystery-tuba-valves", "HISTORY MYSTERY",
        "Which feature was part of the tuba from its earliest patented design?",
        ("Valves", "A slide", "A reed", "A wooden body"), "Valves",
        "Unlike older natural brass instruments, the tuba was designed with valves.", "Tuba",
        "https://www.yamaha.com/en/musical_instrument_guide/tuba/structure/",
    ),

    _question(
        "face-galway-flute", "FAMOUS FACE",
        "Which instrument is concert musician James Galway famous for playing?",
        ("Flute", "Clarinet", "Trombone", "Tuba"), "Flute",
        "James Galway is one of the best-known concert flutists.", "Flute",
        "https://guides.loc.gov/flute/biographies-autobiographies",
    ),
    _question(
        "face-goodman-clarinet", "FAMOUS FACE",
        "Which instrument did jazz bandleader Benny Goodman play?",
        ("Clarinet", "Trumpet", "Flute", "Tuba"), "Clarinet",
        "Benny Goodman was a celebrated jazz clarinetist and bandleader.", "Clarinet",
        "https://www.britannica.com/biography/Benny-Goodman",
    ),
    _question(
        "face-coltrane-sax", "FAMOUS FACE",
        "Which instrument is jazz musician John Coltrane famous for playing?",
        ("Saxophone", "Trumpet", "Trombone", "Flute"), "Saxophone",
        "John Coltrane played tenor and soprano saxophone.", "Saxophone",
        "https://music.si.edu/object-day/john-coltranes-tenor-saxophone",
    ),
    _question(
        "face-davis-trumpet", "FAMOUS FACE",
        "Which instrument is jazz musician Miles Davis famous for playing?",
        ("Trumpet", "Saxophone", "Clarinet", "Trombone"), "Trumpet",
        "Miles Davis was an influential jazz trumpeter and bandleader.", "Trumpet",
        "https://music.si.edu/story/jazz",
    ),
    _question(
        "face-miller-trombone", "FAMOUS FACE",
        "Which instrument did bandleader Glenn Miller play?",
        ("Trombone", "Clarinet", "Trumpet", "Tuba"), "Trombone",
        "Glenn Miller was a trombonist, arranger, and bandleader.", "Trombone",
        "https://www.britannica.com/biography/Glenn-Miller",
    ),
    _question(
        "face-phillips-tuba", "FAMOUS FACE",
        "Which instrument did performer and teacher Harvey Phillips champion?",
        ("Tuba", "Flute", "Saxophone", "Trumpet"), "Tuba",
        "Harvey Phillips was a major tuba performer, teacher, and advocate.", "Tuba",
        "https://jacobsnews.iu.edu/giving/scholarships/scholarships-phillips.html",
    ),
)


def history_mystery_central_date(now: datetime | None = None) -> date:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(HISTORY_MYSTERY_TIMEZONE).date()


def history_mystery_questions_for_date(play_date: date) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for category in HISTORY_MYSTERY_CATEGORIES:
        candidates = [
            question for question in HISTORY_MYSTERY_QUESTIONS
            if question["category"] == category
        ]
        seed = (
            f"{HISTORY_MYSTERY_BANK_VERSION}|{play_date.isoformat()}|{category}"
        ).encode("utf-8")
        index = int.from_bytes(sha256(seed).digest()[:8], "big") % len(candidates)
        selected.append(dict(candidates[index]))
    return selected


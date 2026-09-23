"""Arcade classification; Classroom authorization is intentionally deferred to R6."""
from .age_privacy import require_eligible
from .memberships import student_has_full_access

ALWAYS_FREE = frozenset({'plunge-burrow', 'blue'})
NORMAL = frozenset({'radio-tuner', 'wheel-of-woodchuck', 'scale-keyboard',
                    'thirds', 'dressed-to-the-nines', 'interval-basic-training', 'history-mystery'})
CLASSROOM = {
    'note-names': 'Note Names',
    'instrument-fingerings': 'Instrument Fingerings',
    'rhythm-hear-pick': 'Rhythm — Hear & Pick',
    'key-signatures': 'Key Signatures',
    'transposition': 'Transposition',
}
PACK_COST = 100
PACK_ATTEMPTS = 3


def classroom_authorized(session, profile_id):
    """R6 replaces this with its real relationship/capability lookup, never a client flag."""
    return False


def access_policy(session, profile_id, game_key):
    require_eligible(session, profile_id)
    if game_key in CLASSROOM:
        allowed = classroom_authorized(session, profile_id)
        return {'classification': 'classroom', 'allowed': allowed,
                'free_reason': 'classroom' if allowed else None, 'entry_cost': 0}
    if game_key not in ALWAYS_FREE | NORMAL:
        raise ValueError('That Arcade game is unavailable.')
    reason = ('always_free' if game_key in ALWAYS_FREE else
              'full_access' if student_has_full_access(session, profile_id) else None)
    return {'classification': 'always_free' if game_key in ALWAYS_FREE else 'normal',
            'allowed': True, 'free_reason': reason, 'entry_cost': 0 if reason else PACK_COST}

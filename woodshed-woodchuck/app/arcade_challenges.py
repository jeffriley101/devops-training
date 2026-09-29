"""Server-owned challenges. This JSON lives on the play, never in synced account state."""
from copy import deepcopy
from datetime import timezone
from secrets import choice

GAMES = frozenset({'thirds', 'dressed-to-the-nines', 'interval-basic-training', 'scale-keyboard'})
RUN_SECONDS = 30
ACTION_GRACE_SECONDS = 2
COMPLETION_EARLY_TOLERANCE_SECONDS = 1
# Conservative ceilings prevent instantaneous/batched perfect runs. They are not
# a claim to detect automation; answers still have to match each issued challenge.
MIN_ACTION_SECONDS = {'thirds': .1, 'dressed-to-the-nines': .1,
                      'interval-basic-training': .65, 'scale-keyboard': .05}
THIRDS = tuple(dict(chord=chord, answer=answer) for chord, answer in
               [('C Major', 'E'), ('D Minor', 'F'), ('E Minor', 'G'), ('F Major', 'A'),
                ('G Major', 'B'), ('A Minor', 'C'), ('B Minor', 'D')])
NINES = tuple(dict(tonality=tonality, start=start, answer=answer) for tonality, start, answer in
              [('C Major', 'C', 'D'), ('D Minor', 'D', 'E'), ('F Major', 'F', 'G'),
               ('G Major', 'G', 'A'), ('A Minor', 'A', 'B'), ('Bb Major', 'Bb', 'C'),
               ('Eb Major', 'Eb', 'F')])
INTERVALS = tuple(dict(firstMidi=60, secondMidi=midi, answer=label) for midi, label in
                 [(60, 'Unison'), (62, '2nd'), (64, '3rd'), (65, '4th'), (67, '5th'),
                  (69, '6th'), (71, '7th'), (72, 'Octave'), (74, '9th')])
SCALES = tuple(dict(key=key+'-major', name=name+' Major', rootMidi=midi,
                    notes=[list(pair) for pair in zip([0, 2, 4, 5, 7, 9, 11, 12], names.split())])
               for key, name, midi, names in [
                   ('c', 'C', 60, 'C D E F G A B C'), ('f', 'F', 53, 'F G A B♭ C D E F'),
                   ('g', 'G', 55, 'G A B C D E F♯ G'), ('d', 'D', 50, 'D E F♯ G A B C♯ D'),
                   ('a', 'A', 57, 'A B C♯ D E F♯ G♯ A'), ('e', 'E', 52, 'E F♯ G♯ A B C♯ D♯ E'),
                   ('b', 'B', 59, 'B C♯ D♯ E F♯ G♯ A♯ B')])
BANKS = dict(zip(['thirds', 'dressed-to-the-nines', 'interval-basic-training', 'scale-keyboard'],
                [THIRDS, NINES, INTERVALS, SCALES]))


class ChallengeError(ValueError):
    pass


def elapsed(play, now):
    start = play.started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (now - start).total_seconds()


def _next(game, previous=None):
    return deepcopy(choice([item for item in BANKS[game] if item != previous]))


def initialize(play):
    play.challenge_state = dict(version=1, index=0, score=0, mistakes=0, note_index=0,
                                last_elapsed=0, question=_next(play.game_key), last_action=None,
                                last_result=None)


def snapshot(play, now):
    state = play.challenge_state
    if not state or state.get('version') != 1:
        raise ChallengeError('This older attempt cannot be scored. Start a new game.')
    remaining = max(0, RUN_SECONDS - elapsed(play, now))
    return dict(action_index=state['index'], score=state['score'], mistakes=state['mistakes'],
                note_index=state['note_index'], remaining_ms=int(remaining * 1000),
                finished=play.completed_at is not None or state['mistakes'] >= 2 or remaining == 0,
                question={k: v for k, v in state['question'].items() if k != 'answer'})


def final_score(play, now):
    snapshot(play, now)  # Reject uninitialized legacy plays.
    if elapsed(play, now) < RUN_SECONDS - COMPLETION_EARLY_TOLERANCE_SECONDS and play.challenge_state['mistakes'] < 2:
        raise ChallengeError('The run is still in progress.')
    # Late completion only seals the score already earned before the deadline.
    return play.challenge_state['score']


def accept_action(play, *, action_index, answer, now):
    snapshot(play, now)
    state = deepcopy(play.challenge_state)
    action = dict(action_index=action_index, answer=answer)
    if state['last_action'] == action:
        return dict(challenge=snapshot(play, now), action_result=state['last_result'])
    if play.completed_at is not None or state['mistakes'] >= 2:
        raise ChallengeError('That run has ended.')
    if type(action_index) is not int or action_index != state['index']:
        raise ChallengeError('Answer the current challenge in order.')
    seconds = elapsed(play, now)
    if seconds > RUN_SECONDS + ACTION_GRACE_SECONDS or seconds < 0:
        raise ChallengeError('That run has expired. Complete it and start a new game.')
    if seconds - state['last_elapsed'] < MIN_ACTION_SECONDS[play.game_key]:
        raise ChallengeError('Wait for the current challenge before answering.')
    question = state['question']
    completed = False
    if play.game_key == 'scale-keyboard':
        if type(answer) is not int or not question['rootMidi'] <= answer <= question['rootMidi'] + 12:
            raise ChallengeError('Press a note on the current keyboard.')
        correct = answer == question['rootMidi'] + question['notes'][state['note_index']][0]
        state['score'] = max(0, state['score'] + (100 if correct else -50))
        if correct:
            state['note_index'] += 1
            completed = state['note_index'] == len(question['notes'])
            if completed:
                state['score'] += 500
                state['note_index'] = 0
                state['question'] = _next(play.game_key, question)
    else:
        choices = ('Unison', '2nd', '3rd', '4th', '5th', '6th', '7th', 'Octave', '9th') if play.game_key == 'interval-basic-training' else tuple('ABCDEFG')
        if type(answer) is not str or answer not in choices:
            raise ChallengeError('Choose one of the available answers.')
        correct = answer == question['answer']
        if correct:
            state['score'] += 1
        elif play.game_key == 'interval-basic-training':
            state['mistakes'] += 1
        if state['mistakes'] < 2:
            state['question'] = _next(play.game_key, question)
    state['index'] += 1
    state['last_elapsed'] = seconds
    state['last_action'] = action
    state['last_result'] = dict(correct=correct, completed=completed)
    play.challenge_state = state
    return dict(challenge=snapshot(play, now), action_result=state['last_result'])

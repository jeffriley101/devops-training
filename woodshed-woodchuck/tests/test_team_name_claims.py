"""Public name ownership, independent of Class names and seasonal Team IDs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import teams
from app.models import Season, Team, TeamFamily, TeamNameClaim, WoodchuckProfile
from app.team_name_claims import claim_public_team_name, TEAM_NAME_TAKEN
from tests.test_team_families import family_db, create, counts, NOW
from tests.test_team_continuity import db, source_team, run


def test_public_claims_permanent_names_but_classes_do_not(family_db):
    with family_db() as s:
        first = create(s, family_db)
        assert s.get(TeamNameClaim, 'brass cats').family_id == first.family_id
        claim_public_team_name(s, normalized_name='future name', family_id=first.family_id)
        s.commit()
        assert s.scalar(select(func.count()).select_from(TeamNameClaim)) == 2
        later = Season(key='later', name='Later', starts_on=date(2027, 7, 5), status='planned')
        s.add(later); s.commit()
        classroom = create(s, family_db, private=True, season=later)
        assert classroom.family_id != first.family_id
        assert s.get(TeamNameClaim, 'brass cats').family_id == first.family_id
        before = counts(s)
        with pytest.raises(ValueError, match=TEAM_NAME_TAKEN):
            create(s, family_db, other=True, season=later, name='  BRASS   CATS  ', emblem_key='emoji:dog')
        s.commit()
        assert counts(s) == before
        # Deleting the seasonal Team does not release its name.
        s.delete(first); s.commit()
        assert s.get(TeamNameClaim, 'brass cats') is not None
        with pytest.raises(IntegrityError):
            s.delete(s.get(TeamFamily, first.family_id)); s.flush()
        s.rollback()


def test_class_first_does_not_reserve_public_name_in_later_season(family_db):
    with family_db() as s:
        create(s, family_db, private=True)
        assert s.scalar(select(func.count()).select_from(TeamNameClaim)) == 0
        later = Season(key='later', name='Later', starts_on=date(2027, 7, 5), status='planned')
        s.add(later); s.commit()
        public = create(s, family_db, other=True, season=later)
        assert s.get(TeamNameClaim, 'brass cats').family_id == public.family_id


def test_emblem_conflict_is_specific_and_rolls_back_claim(family_db):
    with family_db() as s:
        create(s, family_db)
        before = counts(s)
        with pytest.raises(ValueError, match='That emblem is already in use this season'):
            create(s, family_db, other=True, name='Other Name')
        s.commit()
        assert counts(s) == before
        assert s.get(TeamNameClaim, 'other name') is None


def test_continuity_reuses_own_claim_and_refuses_foreign_claim(db):
    with db() as s:
        source = source_team(s, db)
        claim = s.get(TeamNameClaim, source.normalized_name)
        other = TeamFamily(); s.add(other); s.flush()
        claim.family_id = other.id; s.commit()
        refused = run(s, db)
        assert refused.classification == 'CONFLICT'
        assert 'public_name_claim_conflict' in refused.teams[0].reasons
        assert s.scalar(select(Team.id).where(Team.season_id == db.ids[1])) is None
        claim.family_id = source.family_id; s.commit()
        applied = run(s, db); s.commit()
        successor = s.get(Team, applied.teams[0].successor_team_id)
        assert successor.family_id == source.family_id
        assert s.get(TeamNameClaim, source.normalized_name).family_id == source.family_id
        assert s.scalar(select(func.count()).select_from(TeamNameClaim)) == 1


def test_cross_season_concurrent_claim_is_atomic(family_db):
    with family_db() as s:
        if s.get_bind().dialect.name != 'postgresql':
            pytest.skip('PostgreSQL concurrent transactions')
        later = Season(key='race-later', name='Later', starts_on=date(2027, 7, 5), status='planned')
        s.add(later); s.commit()
        season_ids = [family_db.test_ids[0], later.id]
    barrier = Barrier(2)
    def worker(index):
        with family_db() as s:
            barrier.wait(timeout=10)
            try:
                create(s, family_db, season=s.get(Season, season_ids[index]), other=bool(index))
                return 'created'
            except ValueError as error:
                s.commit()  # No orphan family or membership may survive failure.
                return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, index) for index in (0, 1)]
        assert sorted(f.result(timeout=20) for f in futures) == sorted(['created', TEAM_NAME_TAKEN])
    with family_db() as s:
        assert counts(s) == (1, 1, 1)
        assert s.scalar(select(func.count()).select_from(TeamNameClaim)) == 1


def old_writer_team(session, factory, *, family=None, season=None, private=False):
    """Insert as the pre-claim application would, without invoking new helpers."""
    if family is None:
        family = TeamFamily()
        session.add(family)
        session.flush()
    row = Team(family_id=family.id,
        season_id=season.id if season else factory.test_ids[0],
        display_name='Brass Cats', normalized_name='brass cats', emblem_key='emoji:cat',
        visibility='private' if private else 'public', director_led=private,
        join_code='OLDCLASS' if private else None)
    session.add(row)
    session.commit()
    assert session.get(TeamNameClaim, row.normalized_name) is None
    return row


def test_old_writer_name_cannot_be_taken_by_new_family(family_db):
    with family_db() as s:
        original = old_writer_team(s, family_db)
        later = Season(key='later', name='Later', starts_on=date(2027, 7, 5), status='planned')
        s.add(later); s.commit()
        before = counts(s)
        with pytest.raises(ValueError, match=TEAM_NAME_TAKEN):
            create(s, family_db, season=later, other=True)
        s.commit()
        assert counts(s) == before  # Includes rollback of the new TeamFamily.
        assert s.get(Team, original.id).family_id == original.family_id
        # The caller owns the transaction: failed creation rolls recovery back
        # too. Existing public rows still protect the name on every retry.
        assert s.get(TeamNameClaim, 'brass cats') is None
        claim_public_team_name(s, normalized_name='brass cats', family_id=original.family_id)
        s.commit()
        assert s.get(TeamNameClaim, 'brass cats').family_id == original.family_id


@pytest.mark.parametrize('seasonal_rows', [1, 2])
@pytest.mark.parametrize('requesting_owner', [True, False])
def test_old_writer_family_recovers_one_claim(family_db, seasonal_rows, requesting_owner):
    with family_db() as s:
        original = old_writer_team(s, family_db)
        if seasonal_rows == 2:
            later = Season(key='later', name='Later', starts_on=date(2027, 7, 5), status='planned')
            s.add(later); s.commit()
            old_writer_team(s, family_db, family=s.get(TeamFamily, original.family_id), season=later)
        if requesting_owner:
            requested_id = original.family_id
        else:
            requester = TeamFamily(); s.add(requester); s.commit()
            requested_id = requester.id
        before = counts(s)
        if requesting_owner:
            claim_public_team_name(s, normalized_name='brass cats', family_id=requested_id)
        else:
            with pytest.raises(ValueError, match=TEAM_NAME_TAKEN):
                claim_public_team_name(s, normalized_name='brass cats', family_id=requested_id)
        s.commit()
        assert s.get(TeamNameClaim, 'brass cats').family_id == original.family_id
        assert s.scalar(select(func.count()).select_from(TeamNameClaim)) == 1
        assert counts(s) == before


def test_old_writer_class_does_not_establish_public_ownership(family_db):
    with family_db() as s:
        classroom = old_writer_team(s, family_db, private=True)
        later = Season(key='later', name='Later', starts_on=date(2027, 7, 5), status='planned')
        s.add(later); s.commit()
        public = create(s, family_db, season=later, other=True)
        assert public.family_id != classroom.family_id
        assert s.get(TeamNameClaim, 'brass cats').family_id == public.family_id


def test_ambiguous_old_writer_ownership_fails_closed(family_db):
    with family_db() as s:
        original = old_writer_team(s, family_db)
        later = Season(key='later', name='Later', starts_on=date(2027, 7, 5), status='planned')
        s.add(later); s.commit()
        other = old_writer_team(s, family_db, season=later)
        requester = TeamFamily(); s.add(requester); s.commit()
        before = counts(s)
        for family_id in (original.family_id, other.family_id, requester.id):
            with pytest.raises(ValueError, match=TEAM_NAME_TAKEN):
                claim_public_team_name(s, normalized_name='brass cats', family_id=family_id)
            s.commit()
            assert s.get(TeamNameClaim, 'brass cats') is None
        assert counts(s) == before
        assert s.get(Team, original.id).family_id != s.get(Team, other.id).family_id


def test_concurrent_missing_claim_recovery_preserves_old_owner(family_db):
    with family_db() as s:
        if s.get_bind().dialect.name != 'postgresql':
            pytest.skip('PostgreSQL concurrent transactions')
        original = old_writer_team(s, family_db)
        other = TeamFamily(); s.add(other); s.commit()
        owner_id, other_id = original.family_id, other.id
    barrier = Barrier(2)
    def worker(family_id):
        with family_db() as s:
            barrier.wait(timeout=10)
            try:
                claim_public_team_name(s, normalized_name='brass cats', family_id=family_id)
                outcome = 'recovered'
            except ValueError as error:
                outcome = str(error)
            s.commit()
            return outcome
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, family_id) for family_id in (owner_id, other_id)]
        assert [f.result(timeout=20) for f in futures] == ['recovered', TEAM_NAME_TAKEN]
    with family_db() as s:
        assert s.get(TeamNameClaim, 'brass cats').family_id == owner_id
        assert s.scalar(select(func.count()).select_from(TeamNameClaim)) == 1

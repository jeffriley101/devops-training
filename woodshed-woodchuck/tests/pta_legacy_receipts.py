"""Synthetic pre-p31 activation fixtures, never a production compatibility path.

Reproduce the former emitter's DML and formats on actual historical schemas so
tests can prove that upgrading does not backfill or authenticate old evidence.
"""
from datetime import date
from sqlalchemy import update
from app import persistent_team_cutover as cutover
from tests.test_persistent_team_cutover import BOUNDARY_AT


def activate_legacy_fixture(db, approved):
    content = approved['content']
    with cutover._write_connection(db[0]) as connection:
        revision = cutover._schema_guard(connection)
        assert revision != 'p31pta001'
        tables = cutover._tables(connection)
        before = cutover._snapshot(connection, tables)
        extensions = cutover.compatibility.classroom_snapshot(connection, revision)
        boundary, closing = cutover._activation_state(before, content, approved['plan_sha256'], BOUNDARY_AT)
        expected = cutover.normalized(before)
        cutover._freeze_closing_roster(connection, tables, before, expected, closing, boundary, BOUNDARY_AT)
        for name, ids, marker in (('teams', content['team_ids'], 'is_operating'),
                                 ('team_memberships', content['membership_ids'], 'is_persistent'),
                                 ('team_join_requests', content['join_request_ids'], 'is_persistent')):
            if ids:
                connection.execute(update(tables[name]).where(tables[name].c.id.in_(ids)).values({marker: True}))
            for row in expected[name]:
                if row['id'] in ids:
                    row[marker] = True
        connection.execute(update(tables['contest_weeks']).where(
            tables['contest_weeks'].c.id.in_(content['prospective_week_ids'])).values(team_membership_rules_version=cutover.PERSISTENT))
        for row in expected['contest_weeks']:
            if row['id'] in content['prospective_week_ids']:
                row['team_membership_rules_version'] = cutover.PERSISTENT
        connection.execute(update(tables['persistent_team_control']).where(
            tables['persistent_team_control'].c.id == 1).values(activated_at=boundary,
                rules_from_week_start=date.fromisoformat(content['rules_from_week_start'])))
        expected['persistent_team_control'][0].update(activated_at=cutover.normalized(boundary),
            rules_from_week_start=content['rules_from_week_start'])
        after = cutover._snapshot(connection, tables)
        assert after == expected
        cutover.compatibility.assert_classroom_unchanged(connection, revision, extensions, cutover.CutoverError)
    receipt = {'transaction_state': 'committed', 'operation': 'activated', 'verification': {
        'passed': True, 'before': cutover._evidence(before), 'after': cutover._evidence(after),
        'historical_attribution_unchanged': True, 'new_teams': 0, 'new_memberships': 0,
        'activated_at': boundary.isoformat(), 'approved_plan_sha256': approved['plan_sha256']}}
    if revision == 'w25weekly005':
        receipt['verification']['evidence_contract'] = 'pta-w25weekly005-v1'
    return receipt

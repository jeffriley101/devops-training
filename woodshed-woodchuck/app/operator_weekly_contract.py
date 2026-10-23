"""Frozen w25weekly005 operator contract, independent of ORM and feature gates.

The five additive migrations are the authority for this literal schema. Legacy
PTA evidence used exactly PROFILE_COLUMNS_V1 before the two nullable additions.
These constants never infer evidence coverage from a receipt hash or live rows.
"""
from sqlalchemy import String, inspect, text

PROFILE_COLUMNS_V1 = ('id',
 'woodchuck_id',
 'display_name',
 'pin_hash',
 'instrument',
 'level',
 'goal',
 'created_at',
 'updated_at',
 'display_name_changed_at',
 'level_changed_at',
 'status',
 'deleted_at',
 'retired_woodchuck_id_hash',
 'session_version',
 'deletion_failed_attempts',
 'deletion_last_failed_at',
 'plunge_best_score')
PROFILE_COLUMNS_W25 = PROFILE_COLUMNS_V1 + ("first_choice_instrument", "game_mode")

W25 = {'weekly_wheel_plays': {'exact': True,
                        'columns': {'id': ('String', 32, False),
                                    'profile_id': ('Integer', None, False),
                                    'request_key': ('String', 64, False),
                                    'funded_day': ('Date', None, False),
                                    'funded_at': ('DateTime', None, False),
                                    'daily_ordinal': ('Integer', None, False),
                                    'outcomes': ('JSON', None, False),
                                    'completed_at': ('DateTime', None, True)},
                        'defaults': {},
                        'primary_key': ('id',),
                        'checks': {'ck_weekly_wheel_daily_ordinal': 'daily_ordinal BETWEEN 1 AND 9'},
                        'pg_checks': {'ck_weekly_wheel_daily_ordinal': 'daily_ordinal >= 1 AND daily_ordinal '
                                                                       '<= 9'},
                        'unique': {'uq_weekly_wheel_day_slot': ('profile_id', 'funded_day', 'daily_ordinal'),
                                   'uq_weekly_wheel_request': ('profile_id', 'request_key')},
                        'foreign_keys': [(('profile_id',), 'woodchuck_profiles', ('id',), 'CASCADE')],
                        'indexes': {'ix_weekly_wheel_plays_profile_id': (('profile_id',), False, None)}},
 'weekly_plans': {'exact': True,
                  'columns': {'id': ('Integer', None, False),
                              'profile_id': ('Integer', None, False),
                              'week_start': ('Date', None, False),
                              'focus': ('String', 64, False),
                              'issued_game_mode': ('String', 20, False),
                              'generator_version': ('String', 40, False),
                              'issued_at': ('DateTime', None, False),
                              'recent_results_cutoff': ('DateTime', None, False),
                              'recent_results': ('JSON', None, False),
                              'logging_days_target': ('Integer', None, False),
                              'practice_minutes_target': ('Integer', None, False),
                              'homework_minutes_target': ('Integer', None, False),
                              'thirds_target': ('Integer', None, False)},
                  'defaults': {'id': 'serial'},
                  'primary_key': ('id',),
                  'checks': {'ck_weekly_plan_cutoff': 'recent_results_cutoff <= issued_at',
                             'ck_weekly_plan_focus': "focus IN ('Build daily consistency', 'Improve tone and "
                                                     "control', 'Learn songs faster', 'Strengthen "
                                                     "improvisation')",
                             'ck_weekly_plan_game_mode': "issued_game_mode IN ('Beginner', 'Challenge', "
                                                         "'Performance')",
                             'ck_weekly_plan_generator': 'length(trim(generator_version)) > 0',
                             'ck_weekly_plan_mode_targets': "(issued_game_mode = 'Beginner' AND "
                                                            'logging_days_target = 3 AND '
                                                            'practice_minutes_target = 30 AND '
                                                            'homework_minutes_target = 10 AND thirds_target '
                                                            "= 3) OR (issued_game_mode = 'Challenge' AND "
                                                            'logging_days_target = 4 AND '
                                                            'practice_minutes_target = 60 AND '
                                                            'homework_minutes_target = 15 AND thirds_target '
                                                            "= 7) OR (issued_game_mode = 'Performance' AND "
                                                            'logging_days_target = 5 AND '
                                                            'practice_minutes_target = 90 AND '
                                                            'homework_minutes_target = 20 AND thirds_target '
                                                            '= 15)'},
                  'pg_checks': {'ck_weekly_plan_cutoff': 'recent_results_cutoff <= issued_at',
                                'ck_weekly_plan_focus': "focus::text = ANY (ARRAY['Build daily "
                                                        "consistency'::character varying, 'Improve tone and "
                                                        "control'::character varying, 'Learn songs "
                                                        "faster'::character varying, 'Strengthen "
                                                        "improvisation'::character varying]::text[])",
                                'ck_weekly_plan_game_mode': 'issued_game_mode::text = ANY '
                                                            "(ARRAY['Beginner'::character varying, "
                                                            "'Challenge'::character varying, "
                                                            "'Performance'::character varying]::text[])",
                                'ck_weekly_plan_generator': 'length(TRIM(BOTH FROM generator_version)) > 0',
                                'ck_weekly_plan_mode_targets': "issued_game_mode::text = 'Beginner'::text "
                                                               'AND logging_days_target = 3 AND '
                                                               'practice_minutes_target = 30 AND '
                                                               'homework_minutes_target = 10 AND '
                                                               'thirds_target = 3 OR issued_game_mode::text '
                                                               "= 'Challenge'::text AND logging_days_target "
                                                               '= 4 AND practice_minutes_target = 60 AND '
                                                               'homework_minutes_target = 15 AND '
                                                               'thirds_target = 7 OR issued_game_mode::text '
                                                               "= 'Performance'::text AND "
                                                               'logging_days_target = 5 AND '
                                                               'practice_minutes_target = 90 AND '
                                                               'homework_minutes_target = 20 AND '
                                                               'thirds_target = 15'},
                  'unique': {'uq_weekly_plan_profile_week': ('profile_id', 'week_start')},
                  'foreign_keys': [(('profile_id',), 'woodchuck_profiles', ('id',), 'CASCADE')],
                  'indexes': {}},
 'weekly_instrument_plans': {'exact': True,
                             'columns': {'plan_id': ('Integer', None, False),
                                         'slot_no': ('Integer', None, False),
                                         'instrument': ('String', 50, False),
                                         'started_at': ('DateTime', None, False),
                                         'homework_spec': ('JSON', None, False)},
                             'defaults': {},
                             'primary_key': ('plan_id', 'slot_no'),
                             'checks': {'ck_weekly_instrument_nonempty': 'length(trim(instrument)) > 0',
                                        'ck_weekly_instrument_slot': 'slot_no IN (1, 2)'},
                             'pg_checks': {'ck_weekly_instrument_nonempty': 'length(TRIM(BOTH FROM '
                                                                            'instrument)) > 0',
                                           'ck_weekly_instrument_slot': 'slot_no = ANY (ARRAY[1, 2])'},
                             'unique': {'uq_weekly_plan_instrument': ('plan_id', 'instrument')},
                             'foreign_keys': [(('plan_id',), 'weekly_plans', ('id',), 'CASCADE')],
                             'indexes': {}},
 'weekly_task_claims': {'exact': True,
                        'columns': {'plan_id': ('Integer', None, False),
                                    'lane': ('String', 10, False),
                                    'claimed_at': ('DateTime', None, False)},
                        'defaults': {},
                        'primary_key': ('plan_id', 'lane'),
                        'checks': {'ck_weekly_task_claim_lane': "lane IN ('board', 'thirds')"},
                        'pg_checks': {'ck_weekly_task_claim_lane': 'lane::text = ANY '
                                                                   "(ARRAY['board'::character varying, "
                                                                   "'thirds'::character varying]::text[])"},
                        'unique': {},
                        'foreign_keys': [(('plan_id',), 'weekly_plans', ('id',), 'CASCADE')],
                        'indexes': {}},
 'weekly_newsletter_presentations': {'exact': True,
                                     'columns': {'profile_id': ('Integer', None, False),
                                                 'week_start': ('Date', None, False),
                                                 'reserved_at': ('DateTime', None, False)},
                                     'defaults': {},
                                     'primary_key': ('profile_id', 'week_start'),
                                     'checks': {},
                                     'pg_checks': {},
                                     'unique': {},
                                     'foreign_keys': [(('profile_id',),
                                                       'woodchuck_profiles',
                                                       ('id',),
                                                       'CASCADE')],
                                     'indexes': {}}}

# These rows must not change during Calendar or PTA operations. PTA already
# checks its entire original row set; Calendar benefits from the same protection.
PRESERVED_TABLES = tuple(W25) + (
    "woodchuck_profiles", "woodchuck_states", "practice_charts", "camp_point_awards",
    "reward_grants", "crown_awards", "crown_progress", "reward_inventory_placements",
    "owned_item_copies", "contest_results", "director_team_contest_results",
)


def validate_profile_and_triggers(connection, error, normalize_sql):
    def refuse(part):
        raise error("weekly_schema_missing_or_changed: " + part)
    inspector = inspect(connection)
    columns = {c["name"]: c for c in inspector.get_columns("woodchuck_profiles")}
    if set(columns) != set(PROFILE_COLUMNS_W25):
        refuse("profile_columns")
    for name, length in (("first_choice_instrument", 50), ("game_mode", 20)):
        c = columns[name]
        if (not isinstance(c["type"], String) or c["type"].length != length
                or not c["nullable"] or c.get("default") is not None
                or c.get("computed") or c.get("identity")):
            refuse("profile_preference_columns")
    checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints("woodchuck_profiles")}
    expected = ("game_mode IS NULL OR (game_mode::text = ANY "
                "(ARRAY['Beginner'::character varying, 'Challenge'::character varying, "
                "'Performance'::character varying]::text[]))"
                if connection.dialect.name == "postgresql" else
                "game_mode IS NULL OR game_mode IN ('Beginner', 'Challenge', 'Performance')")
    if normalize_sql(checks.get("ck_woodchuck_profile_game_mode", "")) != normalize_sql(expected):
        refuse("profile_game_mode_check")
    names = list(W25) + ["woodchuck_profiles"]
    if connection.dialect.name == "postgresql":
        if connection.scalar(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_trigger g JOIN pg_class t ON t.oid=g.tgrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname=current_schema() AND t.relname=ANY(:names)
                    AND (NOT g.tgisinternal OR g.tgenabled <> 'O'))
            OR EXISTS (
                SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname=current_schema() AND t.relname='woodchuck_profiles'
                    AND (NOT c.convalidated OR c.condeferrable OR c.condeferred))
        """), {"names": names}):
            refuse("constraint_or_trigger_enforcement")
    else:
        triggers = connection.execute(text(
            "SELECT tbl_name FROM sqlite_master WHERE type='trigger'"
        )).scalars()
        if any(name in names for name in triggers):
            refuse("unexpected_trigger")

"""Frozen c27 storage contract, independent of live ORM and feature gates."""
C27 = {
    'classroom_chart_director_shares': {
        'exact': True,
        'columns': {'id': ('Integer', None, False), 'chart_id': ('Integer', None, False),
                    'class_id': ('Integer', None, False), 'assignment_id': ('Integer', None, False),
                    'membership_period_id': ('Integer', None, True), 'invitation_id': ('Integer', None, True), 'entitlement_id': ('Integer', None, False),
                    'account_declared_at': ('DateTime', None, False),
                    'class_activation_at': ('DateTime', None, False), 'source_audit_id': ('Integer', None, True),
                    'selected_at': ('DateTime', None, False), 'withdrawn_at': ('DateTime', None, True)},
        'defaults': {'id': 'serial'},
        'checks': {'ck_classroom_chart_director_share_source': '(membership_period_id IS NOT NULL AND invitation_id IS NULL) OR (membership_period_id IS NULL AND invitation_id IS NOT NULL)', 'ck_classroom_chart_director_share_end': 'withdrawn_at IS NULL OR withdrawn_at >= selected_at'},
        'pg_checks': {'ck_classroom_chart_director_share_source': 'membership_period_id IS NOT NULL AND invitation_id IS NULL OR membership_period_id IS NULL AND invitation_id IS NOT NULL', 'ck_classroom_chart_director_share_end': 'withdrawn_at IS NULL OR withdrawn_at >= selected_at'},
        'foreign_keys': [(('invitation_id',), 'classroom_student_invitations', ('id',), 'RESTRICT'), (('chart_id',), 'practice_charts', ('id',), 'CASCADE'),
                         (('class_id',), 'classroom_classes', ('id',), 'RESTRICT'),
                         (('assignment_id',), 'classroom_teaching_assignments', ('id',), 'RESTRICT'),
                         (('membership_period_id',), 'classroom_membership_periods', ('id',), 'RESTRICT'),
                         (('entitlement_id',), 'classroom_entitlements', ('id',), 'RESTRICT'),
                         (('source_audit_id',), 'classroom_s2_audit_events', ('id',), 'RESTRICT')],
        'primary_key': ('id',), 'unique': {},
        'indexes': {'uq_classroom_chart_director_share_open': (('chart_id', 'assignment_id'), True, 'withdrawn_at IS NULL')},
    },
}

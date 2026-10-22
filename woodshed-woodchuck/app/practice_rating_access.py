"""Adult rating response policy, separate from practice and subscription access.

Callers authenticate the adult and authorize ordinary student data first.
No verifier role, legacy director link or Classroom membership grants ratings.
Complimentary/lifetime exceptions and Classroom metric reporting are deferred.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from .age_privacy import eligible
from .memberships import membership_is_active
from .models import (BillingAccount, BillingPaymentEffect, Membership,
                     MembershipSeat, ProviderSubscription)


def adult_rating_allowed(session, profile_id, *, verifier_id=None, guardian=False):
    """A verified guardian or the exact subscription purchaser, with a paid seat.

    guardian=True is supplied only after native consent-bound parent auth.
    Payment history is evidence, not a second entitlement or wallet.
    """
    if not guardian and type(verifier_id) is not int:
        return False
    if not eligible(session, profile_id):
        return False
    now = datetime.now(timezone.utc)
    query = select(Membership).join(MembershipSeat).join(
        BillingAccount, BillingAccount.id == Membership.billing_account_id
    ).join(ProviderSubscription, ProviderSubscription.membership_id == Membership.id).join(
        BillingPaymentEffect, BillingPaymentEffect.subscription_id == ProviderSubscription.id
    ).where(
        MembershipSeat.profile_id == profile_id, MembershipSeat.removed_at.is_(None),
        Membership.source.in_(("stripe", "paypal")),
        ProviderSubscription.provider == Membership.source,
        Membership.access_until.is_not(None),
        BillingPaymentEffect.paid_through >= Membership.access_until,
    )
    if not guardian:
        query = query.where(BillingAccount.verifier_id == verifier_id)
    return any(membership_is_active(member, now)
               for member in session.scalars(query.execution_options(populate_existing=True)))


def rating_metrics_projection(metrics, *, allowed):
    """Allowlist ordinary progress; include rating/trend only after authorization.

    Unknown future fields and aliases do not acquire authority by being added
    to a shared calculator. Never mutate the calculator's input.
    """
    def fields(value, names):
        return {key: value[key] for key in names if key in value}

    totals = ('total', 'verified', 'pristine', 'total_seconds', 'verified_seconds',
              'pristine_seconds', 'charts', 'days')
    result = {key: fields(metrics[key], totals) for key in ('weekly', 'lifetime')}
    if 'practice_streak' in metrics:
        result['practice_streak'] = metrics['practice_streak']
    for key, names in (
        ('season', ('name', 'starts_on', 'ends_on', 'minutes', 'charts', 'days',
                    'verified', 'pristine')),
        ('team', ('name', 'emblem')),
    ):
        if key in metrics:
            result[key] = fields(metrics[key], names) if metrics[key] else None
    if 'achievements' in metrics:
        result['achievements'] = [fields(item, ('key', 'type', 'label', 'icon',
                                               'quantity', 'earned_at'))
                                  for item in metrics['achievements']]
    if 'insights' in metrics:
        result['insights'] = fields(metrics['insights'], (
            'total_seconds', 'average_weekly_seconds', 'total_minutes', 'average_weekly_minutes'))
        result['insights']['weeks'] = [fields(week, (
            'week_start', 'week_end', 'seconds', 'verified_seconds', 'pristine_seconds',
            'minutes', 'days', 'verified_minutes', 'pristine_minutes'))
            for week in metrics['insights']['weeks']]
    if allowed:
        result.update(fields(metrics, ('rating', 'rating_week', 'trend')))
    return result


def verifier_rating_projection(session, verifier_id, snapshot):
    """Selected connection is only a subject selector, never rating authority."""
    from .models import StudentVerifierConnection

    student = snapshot['student']
    if not student:
        return snapshot
    connection = session.get(StudentVerifierConnection, student['connection_id'])
    allowed = bool(connection and connection.verifier_id == verifier_id
                   and connection.status == 'accepted'
                   and adult_rating_allowed(session, connection.profile_id,
                                            verifier_id=verifier_id))
    identity = {key: student[key] for key in (
        'connection_id', 'role', 'display_name', 'instrument', 'level') if key in student}
    return {**snapshot, 'student': {**identity,
            **rating_metrics_projection(student, allowed=allowed)}}

"""Shared server eligibility and conservative historical publication boundaries."""
from datetime import datetime, timezone, time
from sqlalchemy import select
from sqlalchemy.orm import object_session
from fastapi import HTTPException
from .age_models import AccountPrivacy

def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class AgeScreenRequired(HTTPException):
    def __init__(self):
        super().__init__(403, 'Complete age screening before account activity. Help and privacy controls remain available.', headers={'Cache-Control':'no-store'})

def eligible(session, profile_id):
    from .models import WoodchuckProfile
    profile=session.get(WoodchuckProfile,profile_id)
    rule=session.get(AccountPrivacy,profile_id, populate_existing=True)
    if not profile or profile.status!='active' or not rule:return False
    from .child_authorization import consent_active
    if rule.consent_id or rule.age_band=='under13':return consent_active(session,rule)
    return rule.age_band in ('13to17','adult')

def require_eligible(session, profile_id):
    if not eligible(session,profile_id):raise AgeScreenRequired()

def declare_age(session, profile_id, age_band, *, at=None):
    from .models import WoodchuckProfile
    if age_band not in ('under13','13to17','adult','unknown'):raise ValueError('Choose an age band.')
    # Serialize two conflicting declarations, without touching saved account data.
    profile=session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==profile_id).with_for_update().execution_options(populate_existing=True))
    if not profile or profile.status!='active':raise ValueError('Student sign-in is required.')
    rule=session.get(AccountPrivacy,profile_id,populate_existing=True)
    if rule and rule.age_band!='unknown':
        if rule.age_band!=age_band:raise ValueError('A recorded age cannot be changed here. Contact support for a protected correction.')
        return rule
    instant=utc(at or datetime.now(timezone.utc))
    if rule is None:
        rule=AccountPrivacy(profile_id=profile_id);session.add(rule)
    rule.age_band=age_band;rule.declared_at=instant
    rule.public_from=instant if age_band in ('13to17','adult') else None
    rule.private_plunge_best=profile.plunge_best_score or 0
    session.flush();return rule

def can_publish(session, profile_id, *, at=None):
    rule = session.get(AccountPrivacy, profile_id)
    if not rule or rule.age_band not in ('13to17','adult') or rule.public_from is None:
        return False
    if rule.consent_id:
        from .child_authorization import consent_active
        if not consent_active(session,rule):return False
    return at is None or utc(at) >= utc(rule.public_from)

def profile_public(profile):
    session = object_session(profile) if profile is not None else None
    return bool(session and can_publish(session, profile.id))

def feature_allowed(session, profile_id):
    return eligible(session, profile_id)

def sharing_allowed(session, profile_id):
    # Consent-authorized private access never opens generic sharing.
    from .child_authorization import protected_child
    return eligible(session, profile_id) and not protected_child(session,profile_id)


def ordinary_director_allowed(session, profile_id):
    """Only 13+ account activity; consent-linked history remains separately scoped."""
    rule=session.get(AccountPrivacy,profile_id,populate_existing=True)
    return bool(eligible(session,profile_id) and rule.age_band in ('13to17','adult'))


def ordinary_director_connection(session, profile_id, verifier_id):
    from .models import StudentVerifierConnection
    if not ordinary_director_allowed(session,profile_id):return None
    connection=session.scalar(select(StudentVerifierConnection).where(
        StudentVerifierConnection.profile_id==profile_id,
        StudentVerifierConnection.verifier_id==verifier_id,
        StudentVerifierConnection.role=='band_director',
        StudentVerifierConnection.status=='accepted'))
    rule=session.get(AccountPrivacy,profile_id)
    # Earlier accepted parent-authorized directors keep their original scope.
    if rule.consent_id and (not rule.public_from or not connection or
                           utc(connection.invited_at)<utc(rule.public_from)):
        return None
    return connection


def director_chart_visible(session, chart, verifier_id):
    if not ordinary_director_connection(session,chart.profile_id,verifier_id):return False
    rule=session.get(AccountPrivacy,chart.profile_id)
    # Reuse the timestamp AND day-granularity boundary: backdating cannot expose
    # earlier private practice to a new adult, even if submitted after transition.
    return not rule.consent_id or chart_public(session,chart)


def director_scope_start(session, profile_id):
    rule=session.get(AccountPrivacy,profile_id)
    return rule.public_from if rule and rule.consent_id else None

def public_team_identity_allowed(team):
    """Publish only public Team identity, never member or activity details.

    Also permits stored Team medal aggregates under the public Team policy.
    Classes and hidden Teams are always excluded.
    """
    return bool(team is not None and team.visibility == 'public'
                and team.moderation_status != 'hidden')


def team_public(session, team_id, *, at=None, identity_only=False, week=None):
    from .models import Team, TeamMembership, TeamWeekMembershipSnapshot
    team = session.get(Team, team_id)
    if team is None: return False
    members = set(session.scalars(select(TeamMembership.profile_id).where(TeamMembership.team_id == team_id)))
    members.update(session.scalars(select(TeamWeekMembershipSnapshot.profile_id).where(TeamWeekMembershipSnapshot.team_id == team_id)))
    if team.creator_profile_id: members.add(team.creator_profile_id)
    if not all(can_publish(session, pid, at=at) for pid in members):return False
    if identity_only:return True
    from .models import PracticeChart, CampPointAward
    chart_query=select(PracticeChart).where(PracticeChart.team_id==team_id)
    if week is not None:
        chart_query=chart_query.where(PracticeChart.practice_date>=week.week_start, PracticeChart.practice_date<week.week_end,
                                      PracticeChart.include_contests.is_(True), PracticeChart.include_team_contests.is_(True))
    charts=session.scalars(chart_query)
    if not all(chart_public(session,c) for c in charts):return False
    award_query=select(CampPointAward).where(CampPointAward.team_id==team_id)
    if week is not None:
        from zoneinfo import ZoneInfo
        start=datetime.combine(week.week_start,time.min,ZoneInfo('America/Chicago')).astimezone(timezone.utc)
        end=datetime.combine(week.week_end,time.min,ZoneInfo('America/Chicago')).astimezone(timezone.utc)
        award_query=award_query.where(CampPointAward.occurred_at>=start, CampPointAward.occurred_at<end)
    awards=session.scalars(award_query)
    return all(can_publish(session,a.profile_id,at=a.created_at) and can_publish(session,a.profile_id,at=a.occurred_at) for a in awards)

def filter_result_rows(session, rows):
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo
    from .models import PracticeChart, ContestWeek
    def instrument_safe(result):
        if result.subject_type != 'instrument': return True
        from .contests import normalize_instrument
        week = session.get(ContestWeek, result.contest_week_id)
        if week is None: return False
        instrument_key, _ = normalize_instrument(result.instrument or result.display_name_snapshot or '')
        if not instrument_key: return False
        charts = session.scalars(select(PracticeChart).where(PracticeChart.practice_date >= week.week_start,
            PracticeChart.practice_date < week.week_end, PracticeChart.include_contests.is_(True)))
        matching = [c for c in charts if normalize_instrument(c.instrument)[0] == instrument_key]
        # Require evidence for this instrument and never expose private contributions.
        return bool(matching) and all(chart_public(session, c) for c in matching)
    def period_start(result):
        week=session.get(ContestWeek,result.contest_week_id)
        return datetime.combine(week.week_start,time.min,ZoneInfo('America/Chicago')).astimezone(timezone.utc)
    def team_result_safe(row):
        from .models import Team
        result = row[0]
        if result.subject_type != 'team':return True
        return public_team_identity_allowed(session.get(Team, result.team_id)) if result.team_id else False
    return [row for row in rows if instrument_safe(row[0]) and
        (row[0].subject_type != 'student' or can_publish(session, row[0].profile_id, at=period_start(row[0])) and can_publish(session, row[0].profile_id, at=row[0].created_at)) and
        team_result_safe(row)]


def hall_history_allowed(session, profile_id, *, created_at=None):
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    return bool(
        rule
        and rule.age_band in ('13to17', 'adult')
        and not rule.consent_id
        and rule.public_from is not None
        and (created_at is None or utc(created_at) < utc(rule.public_from))
    )


def filter_hall_result_rows(session, rows):
    """Hall-only compatibility for finalized legacy individual results.

    Keep the ordinary historical privacy filter everywhere else. A student result
    rejected only because it predates the new age-screen publication boundary may
    reappear in the Hall once the account currently declares 13+ or adult.
    Unscreened, unknown, under-13 and consent-linked accounts remain excluded.
    Team rows follow the public Team identity policy; instruments retain their
    conservative activity privacy filter. Neither uses this student exception.
    """
    strict = filter_result_rows(session, rows)
    visible_ids = {row[0].id for row in strict}
    output = []
    for row in rows:
        result = row[0]
        if result.id in visible_ids:
            output.append(row)
            continue
        if result.subject_type != 'student' or result.profile_id is None:
            continue
        if not hall_history_allowed(session, result.profile_id, created_at=result.created_at):
            continue
        output.append(row)
    return output



def chart_public(session, chart):
    from zoneinfo import ZoneInfo
    rule=session.get(AccountPrivacy,chart.profile_id)
    return bool(can_publish(session,chart.profile_id,at=chart.created_at) and
        chart.practice_date > utc(rule.public_from).astimezone(ZoneInfo('America/Chicago')).date())


def verifier_can_view(session,profile_id,verifier_id,**kwargs):
    return sharing_allowed(session,profile_id)


class PublicationCacheBoundary:
    """Do not cache privacy-dependent student or other-user projections."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        private=scope['type']=='http' and scope.get('path','').startswith(('/account','/contests','/teams','/director','/arcade','/xp','/trusted-verifiers','/band-director','/practice-charts'))
        async def headers(message):
            if private and message['type']=='http.response.start':
                from starlette.datastructures import MutableHeaders
                message={**message,'headers':list(message['headers'])}
                output=MutableHeaders(scope=message);output['Cache-Control']='no-store';output['Referrer-Policy']='no-referrer'
            await send(message)
        await self.app(scope,receive,headers)


def correct_age(session, profile_id, age_band, *, expected_band, expected_declared_at, actor, case_reference):
    """Support-authorized correction; never a student declaration or consent."""
    import re
    from .models import WoodchuckProfile
    from .age_models import AgeCorrection
    if age_band not in ('13to17', 'adult'):
        raise ValueError('A support correction must establish an eligible age band.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}', case_reference or ''):
        raise ValueError('Use a support case reference only, without personal information.')
    profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == profile_id).with_for_update().execution_options(populate_existing=True))
    rule = session.get(AccountPrivacy, profile_id, populate_existing=True)
    if not profile or profile.status != 'active' or not rule or rule.age_band not in ('under13', 'unknown'):
        raise ValueError('Only an active, restricted declaration can be corrected here.')
    if rule.age_band != expected_band or utc(rule.declared_at).isoformat() != expected_declared_at:
        raise ValueError('The age record changed. Reload before correcting it.')
    instant = datetime.now(timezone.utc)
    previous = rule.age_band
    rule.age_band = age_band
    rule.declared_at = instant
    rule.public_from = instant  # Earlier private history is never made public.
    rule.private_plunge_best = profile.plunge_best_score or 0
    session.add(AgeCorrection(profile_id=profile_id, previous_band=previous,
        corrected_band=age_band, corrected_at=instant, actor_fingerprint=actor,
        case_reference=case_reference, wording_version='support-age-correction-v1'))
    session.flush()
    return rule

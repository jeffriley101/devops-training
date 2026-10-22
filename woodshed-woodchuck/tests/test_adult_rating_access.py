"""Adult rating policy with real endpoints and migrated synthetic history.

Provider/payment rows below are labelled fixture evidence, not real purchases.
Shared rating/practice calculations and the student projection are unchanged.
"""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
from html.parser import HTMLParser

from fastapi.testclient import TestClient
import pytest

from app import family_routes as family
from app.age_models import AccountPrivacy
from app.main import app
from app.models import (BillingAccount, BillingPaymentEffect, BillingProviderEvent,
                        Membership, MembershipSeat, ProviderSubscription,
                        StudentVerifierConnection, TesterEnrollment, TrustedVerifier,
                        WoodchuckProfile)
from app.practice_rating_access import adult_rating_allowed, rating_metrics_projection
from app.security import hash_pin
from tests.test_student_rating_privacy import privacy_db, safe
from tests.test_weekly2_newsletter import db
from tests.test_weekly2_foundation_migration import rows
from test_private_practice import captured, authorize, connect, open_parent, post


def paid_fixture(f, *, pid=1, owner=1, variant='paid', tag='a'):
    """Saved synthetic provider payment/seat authority, never an entitlement mock."""
    now = datetime.now(timezone.utc)
    with f.begin() as s:
        account = BillingAccount(verifier_id=owner);s.add(account);s.flush()
        member = Membership(billing_account_id=account.id, status='active',
            source='manual' if variant=='manual' else 'stripe',
            starts_at=now-timedelta(days=10), access_until=now+timedelta(days=10))
        if variant=='expired':member.access_until=now-timedelta(days=1)
        if variant=='future':member.starts_at=now+timedelta(days=1)
        if variant=='revoked':member.revoked_at=now
        s.add(member);s.flush()
        seat=MembershipSeat(membership_id=member.id, profile_id=pid, slot_number=1)
        if variant=='removed':seat.removed_at=now
        s.add(seat)
        if variant!='manual':
            sub=ProviderSubscription(membership_id=member.id,provider='paypal' if variant=='provider' else 'stripe',
                external_subscription_id='synthetic-'+tag,plan_code='synthetic-full',amount_cents=100,
                currency='USD',interval='month',provider_status='active')
            s.add(sub);s.flush()
            event=BillingProviderEvent(provider='stripe',external_event_id='synthetic-'+tag,
                                      status='processed',payload_hash='0'*64)
            s.add(event);s.flush()
            if variant!='unpaid':
                s.add(BillingPaymentEffect(subscription_id=sub.id,payment_reference='synthetic-'+tag,
                    paid_through=(now-timedelta(days=1) if variant=='short_receipt' else member.access_until),
                    event_id=event.id))
        return member.id


def adult_client():
    c=TestClient(app)
    assert c.post('/trusted-verifiers/login',data={
        'email':'synthetic-verifier@example.test','pin':'1357'}).status_code==200
    return c


def link(f, *, pid=1, role='verifier'):
    with f.begin() as s:
        c=StudentVerifierConnection(profile_id=pid,verifier_id=1,role=role,status='accepted')
        s.add(c);s.flush();return c.id


@pytest.mark.parametrize('variant',[
    'free','paid','unrelated','manual','lifetime','expired','future',
    'revoked','removed','unpaid','provider','short_receipt',
])
def test_verifier_and_purchaser_boundary(privacy_db,variant,record_property):
    f=privacy_db;cid=link(f)
    with f.begin() as s:
        s.add(TrustedVerifier(id=2,email='other@example.test',display_name='Other adult',pin_hash=hash_pin('1357')))
        if variant=='lifetime':
            s.add(TesterEnrollment(profile_id=1,cohort_key='C002',joined_at=datetime.now(timezone.utc)-timedelta(days=1)))
    mid=None
    if variant not in ('free','lifetime'):
        mid=paid_fixture(f,owner=2 if variant=='unrelated' else 1,
                         variant='paid' if variant=='unrelated' else variant)
    allowed=variant=='paid'
    with adult_client() as c:
        before=rows(f.engine)
        page=c.get('/trusted-verifiers/dashboard',params={'connection_id':cid,'profile_id':2})
        assert page.status_code==200
        data=page.context['student']
        with f() as s:
            raw=family.metrics(s,s.get(WoodchuckProfile,1))
            assert adult_rating_allowed(s,1,verifier_id=1) is allowed
        assert data['weekly']==raw['weekly'] and data['lifetime']==raw['lifetime']
        assert data['lifetime']['total_seconds']==439
        if allowed:
            assert data['rating']==raw['rating'] and data['trend']==raw['trend']
            assert 'verifier-rating-card' in page.text
        else:
            safe(data);assert 'verifier-rating-card' not in page.text
            assert 'verifier-trend-' not in page.text
        assert 'FOREIGN PRIVATE RECORD' not in page.text
        assert c.get('/family/practice/data').status_code==401
        assert rows(f.engine)==before
        if allowed:
            # Entitlement loss takes effect without another adult login.
            with f.begin() as s:s.get(Membership,mid).revoked_at=datetime.now(timezone.utc)
            revoked_baseline=rows(f.engine)
            denied=c.get('/trusted-verifiers/dashboard',params={'connection_id':cid})
            assert denied.status_code==200;safe(denied.context['student'])
            assert rows(f.engine)==revoked_baseline
        record_property('rating_allowed',allowed)
        record_property('all_business_rows_unchanged',True)


@pytest.mark.parametrize('paid_students',[(),(1,),(1,2)])
def test_director_html_csv_and_aggregate_boundary(privacy_db,paid_students):
    f=privacy_db
    for pid in (1,2):link(f,pid=pid,role='band_director')
    if paid_students:
        mid=paid_fixture(f)
        if 2 in paid_students:
            with f.begin() as s:s.add(MembershipSeat(membership_id=mid,profile_id=2,slot_number=2))
    with adult_client() as c:
        before=rows(f.engine)
        page=c.get('/band-director/dashboard');assert page.status_code==200,page.text
        people=page.context['students'];assert len(people)==2
        for pid,person in enumerate(people,1):
            with f() as s:raw=family.metrics(s,s.get(WoodchuckProfile,pid))
            assert person['lifetime']==raw['lifetime']
            if pid in paid_students:
                assert person['rating']==raw['rating'] and person['trend']==raw['trend']
            else:safe(person)
        assert ('program_rating' in page.context)==(len(paid_students)==2)
        assert ('PROGRAM RATING' in page.text)==(len(paid_students)==2)
        export=c.get('/band-director/dashboard.csv');assert export.status_code==200
        records=list(csv.DictReader(StringIO(export.text)));assert len(records)==2
        for pid,row in enumerate(records,1):
            assert bool(row['Practice Rating'])==(pid in paid_students)
            assert bool(row['Trend'])==(pid in paid_students)
            assert int(row['Career Practice Seconds'])==(439 if pid==1 else 59940)
        if not paid_students:
            assert 'bd-trend-' not in page.text
            class Cells(HTMLParser):
                def __init__(self):
                    super().__init__();self.body=False;self.rows=[];self.cell=None
                def handle_starttag(self,tag,attrs):
                    if tag=='tbody':self.body=True
                    if self.body and tag=='tr':self.rows.append([])
                    if self.body and tag=='td':
                        self.cell={'attrs':dict(attrs),'text':''};self.rows[-1].append(self.cell)
                def handle_data(self,text):
                    if self.cell is not None:self.cell['text']+=text
                def handle_endtag(self,tag):
                    if tag=='td':self.cell=None
                    if tag=='tbody':self.body=False
            parsed=Cells();parsed.feed(page.text)
            assert len(parsed.rows)==2
            for cells in parsed.rows:
                for cell in cells[:2]:
                    assert cell['attrs']=={}  # no hidden rating/delta sort value
                    assert cell['text']=='Unavailable'

        assert rows(f.engine)==before


@pytest.mark.parametrize('paid',[False,True])
def test_kws_parent_and_legacy_director_boundary(privacy_db,captured,paid):
    f=privacy_db
    # The native director flow allocates a verifier; this fixture already seeds id1.
    # SQLite advances its integer key correctly; PostgreSQL coverage is separate.
    with f.begin() as s:
        rule=s.get(AccountPrivacy,1);rule.age_band='unknown';rule.public_from=None
    child,_,pid,wid=authorize(f,captured)
    director=connect(captured[0]);open_parent(child,captured,wid)
    if paid:paid_fixture(f)
    before=rows(f.engine)
    parent=child.get('/family/parent/data');assert parent.status_code==200
    pmetrics=parent.json()['metrics']
    page=child.get('/family/parent');assert page.status_code==200
    if paid:
        with f() as s:raw=family.metrics(s,s.get(WoodchuckProfile,pid))
        assert pmetrics['rating']==raw['rating'] and pmetrics['trend']==raw['trend']
        assert 'Rating:' in page.text
    else:
        safe(pmetrics);safe(page.context['metrics']);assert 'Rating:' not in page.text
    # A named legacy director does not become a Classroom or purchaser authority.
    d=director.get('/family/director/data');assert d.status_code==200;safe(d.json()['metrics'])
    html=director.get('/family/director');assert html.status_code==200;safe(html.context['metrics'])
    assert pmetrics['lifetime']['total_seconds']==439
    safe(child.get('/family/practice/data').json()['metrics'])
    assert rows(f.engine)==before
    assert post(child,'/family/parent/withdraw',{'withdraw':'yes'},page='/family/parent').status_code==200
    before=rows(f.engine)
    assert child.get('/family/parent/data').status_code==403
    assert director.get('/family/director/data').status_code==403
    assert rows(f.engine)==before


def test_purchaser_cannot_expand_seat_scope(privacy_db):
    f=privacy_db;a=link(f);b=link(f,pid=2)
    paid_fixture(f,pid=2)
    with adult_client() as c:
        before=rows(f.engine)
        first=c.get('/trusted-verifiers/dashboard',params={'connection_id':a})
        second=c.get('/trusted-verifiers/dashboard',params={'connection_id':b})
        assert first.status_code==second.status_code==200
        safe(first.context['student'])
        assert 'rating' in second.context['student']
        assert first.context['student']['display_name']=='Synthetic A'
        assert second.context['student']['display_name']=='Synthetic B'
        with f.begin() as s:
            s.add(TrustedVerifier(id=2,email='foreign@example.test',display_name='Foreign',pin_hash=hash_pin('1357')))
            s.flush()
            connection=s.get(StudentVerifierConnection,b);connection.verifier_id=2
        revoked_baseline=rows(f.engine)
        assert c.get('/trusted-verifiers/dashboard',params={'connection_id':b}).status_code==404
        assert rows(f.engine)==revoked_baseline


def test_unknown_rating_aliases_fail_closed():
    raw={'weekly':{'charts':1,'rating_alias':99},'lifetime':{'total_seconds':300,'rating':99},
         'rating':99,'trend':{'delta':99,'direction':'up'},'rating_week_values':[99],
         'score':99,'history':[99],'practice_streak':1,'season':None,'team':None,
         'achievements':[{'label':'Saved','score':99}],
         'insights':{'total_seconds':300,'score':99,'weeks':[{'seconds':300,'rating':99}]}}
    saved=deepcopy(raw);projected=rating_metrics_projection(raw,allowed=False)
    safe(projected);assert raw==saved and projected['lifetime']=={'total_seconds':300}

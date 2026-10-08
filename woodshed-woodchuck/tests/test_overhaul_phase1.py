"""Approved Phase 1 presentation boundaries against the C002 release lineage."""
from html.parser import HTMLParser
from pathlib import Path

import pytest
from app.content import PRACTICE_DEFINITION
from test_world_entry import client, login

ROOT = Path(__file__).resolve().parents[1]


class Structure(HTMLParser):
    def __init__(self, markup):
        super().__init__();self.ids=[];self.forms=[];self.current_form=None;self.nested=False
        self.feed(markup)
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if tag=='form':
            self.nested |= self.current_form is not None
            self.current_form=attrs.get('id');self.forms.append(self.current_form)
    def handle_endtag(self, tag):
        if tag=='form':self.current_form=None


@pytest.mark.parametrize('path', ['/home','/store','/p-book','/quest'])
def test_reconstructed_surfaces_have_no_global_tabs_or_adult_footer(client,path):
    login(client)
    response=client.get(path);assert response.status_code==200
    markup=response.text
    assert 'class="main-nav"' not in markup
    assert 'href="/family/practice"' not in markup
    assert 'href="/family/parent-access"' not in markup
    structure=Structure(markup)
    assert len(structure.ids)==len(set(structure.ids))
    assert not structure.nested


@pytest.mark.parametrize('path', ['/practice/skill-building','/practice/pristine','/account/privacy','/membership?as_account=student','/plunge-burrow','/arcade'])
def test_nested_destinations_keep_safe_room_navigation(client,path,monkeypatch):
    from app import main, membership_routes
    monkeypatch.setattr(membership_routes, "SessionLocal", main.SessionLocal)
    login(client)
    response=client.get(path)
    assert response.status_code==200
    markup=response.text
    assert 'href="/home"' in markup or 'href="/store"' in markup


def test_parent_access_is_retained_on_welcome(client):
    assert 'href="/family/parent-access"' in client.get('/').text


def test_combined_profile_panels_keep_separate_forms_and_authoritative_requests(client):
    login(client)
    markup=client.get('/home').text
    identity=markup[markup.index('id="shed-team-panel"'):markup.index('id="shed-secret-panel"')]
    assert 'id="change-name-form"' in identity
    for control in ['shed-team-options','shed-team-create','shed-private-team-request','shed-team-report-submit','shed-director-team-link']:
        assert f'id="{control}"' in identity
    musician=markup[markup.index('<dialog id="your-woodchuck"'):].split('</dialog>')[0]
    assert 'id="woodchuck-editor-form"' in musician and 'id="change-level-form"' in musician
    assert not Structure(musician).nested
    account=(ROOT/'static/js/account.js').read_text()
    for endpoint in ['/account/profile/name','/account/profile/level']:assert endpoint in account
    assert "fetch('/account/appearance'" in (ROOT/'static/js/woodchuck-appearance.js').read_text()


def test_contact_is_single_panel_with_existing_destinations(client):
    login(client)
    markup=client.get('/store').text
    contact=markup.split('data-shop-panel-content="contact"')[1].split('</section>')[0]
    for content in ['Email Woodshed Support','mailto:support@woodshedwoodchuck.com','shop-qr-image','Account &amp; Privacy','id="authenticated-logout"']:
        assert content in contact
    assert markup.count('id="authenticated-logout"')==1
    assert 'data-shop-panel="share"' not in markup and 'data-shop-panel="artist"' not in markup


def test_definition_is_exact_and_only_on_board_above_bonus(client):
    login(client)
    board=client.get('/quest').text
    card=board.split('class="board-definition-card"')[1].split('</aside>')[0]
    assert PRACTICE_DEFINITION in card
    assert board.index('class="board-definition-card"') < board.index('class="board-practice-section bonus-challenge-section"')
    for path in ['/home','/store','/p-book']:assert PRACTICE_DEFINITION not in client.get(path).text

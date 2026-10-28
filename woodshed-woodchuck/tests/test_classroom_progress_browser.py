"""Firefox: native explicit consent and withdrawal at desktop and narrow mobile widths."""
import socket
import sys
from threading import Thread
import time

import pytest
from playwright.sync_api import sync_playwright, expect
import uvicorn

from app.main import app
from app.models import WoodchuckState
from app.classroom_progress_authorization import LABEL
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web
from test_classroom_progress_routes import progress_web


@pytest.fixture
def progress_site(progress_web, monkeypatch):
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", progress_web)
    with progress_web() as session:
        session.add(WoodchuckState(profile_id=1, revision=0, state_json={
            "account": {"woodchuckId": "WC-SYNTHETIC", "authenticated": True, "serverRevision": 0},
            "profile": {"woodchuckName": "Private Student", "instrument": "Trumpet",
                        "level": "Beginner", "goal": "Build daily consistency"},
        }))
        session.commit()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        monkeypatch.setenv("PUBLIC_BASE_URL", origin)
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                assert thread.is_alive()
                time.sleep(.05)
            assert server.started
            yield origin
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844), (320, 740)])
def test_firefox_explicit_choice_and_withdrawal(progress_site, tmp_path, width, height):
    origin = progress_site
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch()
        context = browser.new_context(base_url=origin, viewport={"width": width, "height": height}, reduced_motion="reduce")
        response = context.request.post("/account/login", form={"woodchuck_id": "WC-SYNTHETIC", "pin": "2468"}, headers={"Origin": origin})
        assert response.status == 200
        page = context.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("/home#shed-team-panel")
        page.get_by_role("link", name="Classroom reporting", exact=True).click()
        expect(page.get_by_role("heading", name="Classroom reporting", exact=True)).to_be_visible()
        checkbox = page.get_by_role("checkbox", name=LABEL)
        expect(checkbox).not_to_be_checked()
        assert "Comprehensive reporting is not available yet" in page.locator("main").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        save = page.get_by_role("button", name="Save authorization", exact=True)
        assert save.bounding_box()["height"] >= 44
        save.click()
        expect(checkbox).not_to_be_checked()
        assert page.get_by_role("button", name="Withdraw authorization").count() == 0
        page.screenshot(path=str(tmp_path / f"classroom-reporting-choice-{width}.png"), full_page=True)
        checkbox.focus(); page.keyboard.press("Space")
        expect(checkbox).to_be_checked()
        save.focus(); page.keyboard.press("Enter")
        withdraw = page.get_by_role("button", name="Withdraw authorization", exact=True)
        expect(withdraw).to_be_visible()
        assert page.get_by_role("checkbox").count() == 0
        page.reload()
        expect(withdraw).to_be_visible()
        page.screenshot(path=str(tmp_path / f"classroom-reporting-saved-{width}.png"), full_page=True)
        withdraw.focus(); page.keyboard.press("Enter")
        expect(page.get_by_role("status")).to_contain_text("Authorization withdrawn")
        expect(page.get_by_role("checkbox", name=LABEL)).not_to_be_checked()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        browser.close()

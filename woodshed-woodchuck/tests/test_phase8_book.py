from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_book_has_four_default_checked_option_cards_and_exact_copy() -> None:
    html = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    for control in (
        "p-book-include-contests", "p-book-include-team",
        "p-book-email-copy", "p-book-request-validation",
    ):
        assert f'id="{control}"' in html
        assert f'id="{control}"' in html and "checked" in html.split(f'id="{control}"', 1)[1].split(">", 1)[0]
    assert "Include this chart in Band Camp contests" in html
    assert "Include this chart in the Team Competition" in html
    assert "Uncheck to prevent being added to this contest." in html
    assert "Email your Practice Book to someone that agrees to receive your emails" in html
    assert "Uncheck to prevent emailing anyone." in html
    assert "Request your parent or mentor to validate your P-Chart" in html
    assert "Uncheck to prevent sending a notification." in html
    assert html.index("p-book-email-copy") < html.index("p-book-email-preset") < html.index("p-book-request-validation")


def test_book_uses_one_confirmation_and_no_old_copy_or_email_actions() -> None:
    html = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    assert ">Submit P-Chart</button>" in html
    assert "Submit this P-Chart?" in html
    assert "It will be saved to your Practice Book and copied to your clipboard." in html
    assert "Copy to Clipboard" not in html
    assert "Email Your Chart" not in html
    assert "Trusted verifier (optional)" not in html
    assert "No trusted verifiers are connected yet. This chart can still be saved as Open." not in html


def test_missing_selection_and_gold_pulse_architecture() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    for label in (
        "Choose a Team", "Submit Without Team Competition",
        "Choose a Recipient", "Submit Without Emailing",
        "Choose a Parent or Mentor", "Submit Without Validation Request",
    ):
        assert label in script
    assert "p-book-email-copy" not in script.split("function updateSubmitGlow", 1)[1].split("}", 1)[0]
    assert "p-book-submit-gold" in script and "p-book-submit-gold" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "navigator.clipboard.writeText" in script
    assert "P-Chart saved, but it could not be copied automatically." in script

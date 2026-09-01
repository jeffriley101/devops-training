import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates/base.html").read_text(encoding="utf-8")
HOME = (ROOT / "templates/home.html").read_text(encoding="utf-8")
BOOK = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
BOARD = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
SHOP = (ROOT / "templates/store.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
APP = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
REACTION = (ROOT / "static/js/character-reaction.js").read_text(encoding="utf-8")


def test_mum_artwork_is_used_only_inside_the_existing_mum_experience() -> None:
    panel = HOME[HOME.index('id="mum-panel"'):]
    assert '/static/img/characters/mum.png' in panel
    assert '/static/img/characters/mum-corner.png' in panel
    assert panel.index("mum-presence-art") < panel.index("mum-corner-art")
    assert 'id="mum-message"' in panel
    assert 'id="mum-snack-chooser"' in panel
    assert 'id="mum-ready-button"' in panel


def test_whistler_and_john_are_reactions_not_static_page_art() -> None:
    timer = BOOK[BOOK.index('class="mentor-card practice-timer-metal"'):BOOK.index('</article>')]
    assert '/static/img/characters/whistler.png' not in BOOK
    assert 'p-book-whistler-art' not in timer

    bonus = BOARD[BOARD.index('class="board-practice-section bonus-challenge-section"'):]
    assert '/static/img/characters/john.png' not in BOARD
    assert 'bonus-challenge-john-art' not in bonus

    assert 'imageUrl: "/static/img/characters/whistler.png"' in APP
    assert 'characterName: "Coach Whistler"' in APP
    assert 'message: "Whee-oo-wheet! ... Good job!"' in APP
    assert 'imageUrl: "/static/img/characters/john.png"' in APP
    assert 'message: "Ah geez, I\'ve been trying to play that all day!"' in APP
    assert "Your practice coach" not in APP
    assert "Your curious practice buddy" not in APP


def test_character_art_is_responsive_and_cannot_intercept_controls() -> None:
    for asset in ("mum.png", "mum-corner.png", "whistler.png", "john.png"):
        path = ROOT / "static" / "img" / "characters" / asset
        assert path.is_file() and path.stat().st_size > 0
    assert ".mum-artwork img" in CSS
    assert ".ww-character-reaction" in CSS
    assert ".ww-character-reaction img" in CSS
    assert CSS.count("pointer-events: none;") >= 3
    narrow = CSS[CSS.index("@media (max-width: 430px)"):]
    assert ".mum-artwork" in narrow
    assert ".ww-character-reaction" in narrow
    assert "width: 100%; height: 100%; max-height: 100%;" in narrow

    reaction_start = CSS.index(".ww-character-reaction {")
    reaction_end = CSS.index(".p-book-page .pirate-logbook {", reaction_start)
    reaction_css = CSS[reaction_start:reaction_end]
    assert "inset: 0;" in reaction_css
    assert "width: 100%;" in reaction_css and "height: 100%;" in reaction_css
    assert "grid-template-rows: auto minmax(0, 1fr);" in reaction_css
    assert "width: min(48rem, 100%);" in reaction_css
    assert "height: 100%;" in reaction_css and "object-fit: contain;" in reaction_css
    assert "object-position: center top;" in reaction_css
    assert "position: fixed;" in reaction_css
    assert "100vw" not in reaction_css


def test_reactions_only_follow_newly_persisted_successes() -> None:
    quest = APP[APP.index("function wireQuestForm"):APP.index("const STORE_ITEMS")]
    bonus = quest[quest.index('form.addEventListener("submit", async function (event)'):]
    assert bonus.index('if (payload.created === true) {') < bonus.index('characterName: "John"')
    assert bonus.index('characterName: "John"') < bonus.index("} catch (error) {")

    pbook = APP[APP.index("function wirePBook"):]
    whistler = pbook.index('characterName: "Coach Whistler"')
    success_guard = pbook.rindex("if (createdPayload.created === true) {", 0, whistler)
    failure = pbook.index("} catch (error) {", whistler)
    assert success_guard < whistler < failure
    assert "This P-Chart was already saved. No duplicate actions were performed." in pbook


def test_shared_reaction_persists_until_card_or_close_button_dismissal() -> None:
    assert 'dismiss.type = "button"' in REACTION
    assert 'dismiss.setAttribute("aria-label", "Dismiss character reaction")' in REACTION
    assert 'reaction.addEventListener("click", dismissCharacterReaction)' in REACTION
    assert 'dismiss.addEventListener("click"' in REACTION
    assert "setTimeout" not in REACTION
    assert "clearTimeout" not in REACTION
    assert "durationMs" not in REACTION
    assert "localStorage" not in REACTION and "sessionStorage" not in REACTION
    assert 'audio.play("characterWhistle")' in REACTION
    assert 'getElementById("authenticated-player-name")' in REACTION
    assert 'quote.textContent = `: “${personalizedMessage(settings.message)}”`' in REACTION
    assert "innerHTML" not in REACTION
    assert 'id="authenticated-player-name"' in BASE
    assert "authenticated_profile.display_name" in BASE
    assert "| tojson" in BASE
    assert REACTION.index("reaction.append(speech, image, dismiss)") < REACTION.index(
        "playCharacterWhistle();"
    )

    source = r'''
const assert = require("node:assert/strict");
let whistleCalls = 0;
const playerNameData = { textContent: JSON.stringify('Alex "Amp" <script>&') };
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {};
    this.className = ""; this.classList = { add() {}, remove() {} };
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  remove() { this.removed = true; }
}
global.window = global;
global.document = {
  body: new Element("body"),
  createElement: (tag) => new Element(tag),
  getElementById: (id) => id === "authenticated-player-name" ? playerNameData : null,
};
global.requestAnimationFrame = (callback) => callback();
global.WoodshedAudio = {
  play(name) { assert.equal(name, "characterWhistle"); whistleCalls += 1; return true; },
};
require("./static/js/character-reaction.js");
assert.equal(WoodshedCharacterReaction.show({
  characterName: "Coach Whistler",
  imageUrl: "/whistler.png",
  message: "Whee-oo-wheet! ... Good job!",
}), true);
const whistler = document.body.children[0];
assert.equal(whistler.children[0].children[0].textContent, "Coach Whistler");
assert.equal(
  whistler.children[0].children[1].textContent,
  ': “Whee-oo-wheet! ... Good job, Alex "Amp" <script>&!”'
);
assert.equal(whistler.children[0].children[1].children.length, 0);
assert.equal(whistler.children[1].src, "/whistler.png");
assert.equal(whistleCalls, 1);
assert.notEqual(whistler.removed, true);
whistler.listeners.click();
assert.equal(whistler.removed, true);

assert.equal(WoodshedCharacterReaction.show({
  characterName: "John",
  imageUrl: "/john.png",
  message: "Ah geez, I've been trying to play that all day!",
}), true);
const john = document.body.children[1];
assert.equal(john.children[0].children[0].textContent, "John");
assert.equal(
  john.children[0].children[1].textContent,
  ': “Ah geez, I\'ve been trying to play that all day, Alex "Amp" <script>&!”'
);
assert.equal(whistleCalls, 2);
assert.notEqual(john.removed, true);
let propagationStopped = false;
john.children[2].listeners.click({ stopPropagation() { propagationStopped = true; } });
assert.equal(propagationStopped, true);
assert.equal(john.removed, true);
WoodshedAudio.play = function () { throw new Error("blocked"); };
playerNameData.textContent = JSON.stringify("   ");
assert.equal(WoodshedCharacterReaction.show({
  characterName: "Coach Whistler",
  imageUrl: "/whistler.png",
  message: "Whee-oo-wheet! ... Good job!",
}), true);
const silentWhistler = document.body.children[2];
assert.equal(
  silentWhistler.children[0].children[1].textContent,
  ': “Whee-oo-wheet! ... Good job!”'
);
assert.notEqual(silentWhistler.removed, true);
silentWhistler.listeners.keydown({ key: "Escape" });
assert.equal(silentWhistler.removed, true);

playerNameData.textContent = "null";
assert.equal(WoodshedCharacterReaction.show({
  characterName: "John",
  imageUrl: "/john.png",
  message: "Ah geez, I've been trying to play that all day!",
}), true);
const genericJohn = document.body.children[3];
assert.equal(
  genericJohn.children[0].children[1].textContent,
  ': “Ah geez, I\'ve been trying to play that all day!”'
);
console.log(JSON.stringify({
  whistlerDismissed: whistler.removed,
  johnDismissed: john.removed,
  visibleAfterAudioFailure: true,
  escapeDismissed: silentWhistler.removed,
  genericJohnVisible: !genericJohn.removed,
  whistleCalls,
}));
'''
    result = subprocess.run(
        ["node", "-e", source], cwd=ROOT, check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {
        "whistlerDismissed": True,
        "johnDismissed": True,
        "visibleAfterAudioFailure": True,
        "escapeDismissed": True,
        "genericJohnVisible": True,
        "whistleCalls": 2,
    }


def test_shop_uses_the_approved_viking_artwork_without_removing_legacy_art() -> None:
    combined = "\n".join((HOME, BOOK, BOARD, SHOP, CSS))
    assert '/static/img/shop-viking-valley-fair.png' in SHOP
    assert '/static/img/shop3.png' not in SHOP
    assert (ROOT / "static/img/shop-viking-valley-fair.png").is_file()
    assert (ROOT / "static/img/shop3.png").is_file()
    assert "Viking at the Whimsical Valley Fair.png" not in combined

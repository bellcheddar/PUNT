"""The ten cards as a one-stop shop.

After the first real Sunday the cards were asked to carry everything a manager
checks: the chance of winning, who they are playing, how many starters have
played, are playing and are still to go. And the big score is coloured by the
chance of winning the head-to-head, not by its size.
"""

from __future__ import annotations

import re

import pytest

from views.viewmodels import WIN_TONES, album_view, win_tone


@pytest.mark.parametrize("probability, tone", [
    (None, ""), (0.0, "red"), (0.399, "red"), (0.40, "amber"),
    (0.5, "amber"), (0.599, "amber"), (0.60, "green"), (1.0, "green"),
])
def test_the_score_colour_follows_the_chance_of_winning(probability, tone):
    assert win_tone(probability) == tone


def test_the_tones_are_symmetric_round_a_coin_flip():
    (green, _), (amber, _) = WIN_TONES
    assert round(green - 0.5, 6) == round(0.5 - amber, 6)


def test_every_starter_is_played_on_or_to_go(repo, no_network):
    snap = repo.snapshot()
    for card in album_view(snap):
        if card["missing"]:
            continue  # pragma: no cover - the fixture has no orphan teams
        counted = card["played"] + card["playing"] + card["to_play"]
        assert counted == len(card["starters"]), card["name"]


def test_a_card_knows_its_opponent_by_team_name(repo, no_network):
    snap = repo.snapshot()
    names = {t.name for t in snap.teams}
    abbrevs = {t.abbrev for t in snap.teams} - names
    for card in album_view(snap):
        assert card["opponent"] in names
        assert card["opponent"] not in abbrevs
        assert card["margin"] == pytest.approx(card["total"] - card["opponent_total"], abs=0.02)


def test_the_card_shows_what_it_says_it_shows(client, no_network):
    html = client.get("/partials/album").get_data(as_text=True)
    cards = re.findall(r'<article class="card .*?</article>', html, flags=re.S)
    assert len(cards) == 10
    for card in cards:
        assert "PLAYED" in card and "TO GO" in card
        assert re.search(r"WIN \d+%", card)
        tone = re.search(r'card-score card-score--(green|amber|red)', card)
        win = int(re.search(r"WIN (\d+)%", card).group(1))
        assert tone, "a card with a win chance must colour its score"
        # The rounded percentage can sit on a boundary the raw number did not
        # cross, so only the unambiguous cases are asserted.
        if win >= 62:
            assert tone.group(1) == "green"
        elif win <= 38:
            assert tone.group(1) == "red"

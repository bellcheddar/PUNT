"""Every panel that shows a number has to refresh itself.

Three panels shipped static: bench regret, playoff odds and who is in trouble
were rendered once when the page loaded and never again, so a phone left on the
bar showed two o'clock's numbers at five and nothing on screen said so. They
were correct on arrival, which is exactly why nobody caught it: every
screenshot, every manual check and every existing test looked at a page that had
just been loaded.

The test is therefore not "is this panel right", it is "is this panel wired to
become right again". It is written to catch a panel that is ADDED without a
trigger, which is how the original three got in.
"""

from __future__ import annotations

import re

import pytest

ROUTES = ["/", "/album", "/cheer", "/swing", "/receipts", "/multiverse", "/big-board?tv=1"]

#: Panels allowed to render once and stop, each with the reason. This is an
#: explicit list on purpose. The first version of this test skipped any panel
#: with no digits in its markup, on the theory that a panel with no numbers has
#: nothing to go stale -- and that made the check depend on what the fixture
#: happened to be showing at the moment it ran. Stripping the trigger off
#: "Biggest swings today" did not fail it, because that panel was rendering its
#: empty state and the empty state has no digits in it. A check that passes on a
#: broken page because of what the data looked like is not a check.
LOADS_ONCE = {
    # Generated prose about a finished week. Rewriting it every thirty seconds
    # would churn the wording while nobody is reading a different afternoon.
    "The week, in short",
}


def _panels(html: str):
    main = html.split("<main", 1)[1].split("</main>")[0] if "<main" in html else html
    for chunk in re.split(r'<section class="panel', main)[1:]:
        found = re.search(r'<h2 class="panel-title">(.*?)</h2>', chunk, re.S)
        title = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", found.group(1))).strip() if found else ""
        yield title, chunk


@pytest.mark.parametrize("route", ROUTES)
def test_no_panel_with_numbers_in_it_is_static(client, route, no_network):
    html = client.get(route + ("&" if "?" in route else "?") + "punt=steady").get_data(as_text=True)
    panels = list(_panels(html))
    assert panels, f"{route} rendered no panels at all"
    for title, chunk in panels:
        if any(title.startswith(allowed) for allowed in LOADS_ONCE):
            assert 'hx-trigger="load"' in chunk, f"{title} should at least load"
            continue
        assert "hx-trigger" in chunk and "every" in chunk, (
            f"{route}: the {title!r} panel never refreshes itself. "
            f"Wrap it in a div with hx-get and hx-trigger=\"every Ns\", or add it "
            f"to LOADS_ONCE with a reason."
        )


@pytest.mark.parametrize("name", ["regret", "trouble", "odds", "swings", "allplay", "luck"])
def test_every_panel_fragment_answers(client, name, no_network):
    for query in ("", "?full=1"):
        response = client.get(f"/partials/panel/{name}{query}")
        assert response.status_code == 200, f"{name}{query}"
        assert response.data.strip(), f"{name}{query} rendered nothing"


def test_an_unknown_panel_is_a_404_not_a_500(client, no_network):
    """The name goes into a template path, so an unknown one must not reach it."""
    assert client.get("/partials/panel/bogus").status_code == 404
    assert client.get("/partials/panel/..%2F..%2Fbase").status_code == 404


def test_the_polled_fragment_matches_what_the_page_rendered(client, no_network):
    """The fragment htmx swaps in must be the same markup the page shipped with.

    They are one template, included by the page and rendered by the route, so
    this is really a test that they have not been forked again: the Receipts tab
    spent months leading every row with a username after the rest of the app had
    moved to team names, because it had its own copy.
    """
    page = client.get("/?punt=steady").get_data(as_text=True)
    fragment = client.get("/partials/panel/regret").get_data(as_text=True)
    rows = re.findall(r'data-sheet="[^"]*detail/regret/(\d+)"', fragment)
    assert rows, "the fragment has no regret rows in it"
    for team_id in rows:
        assert f"detail/regret/{team_id}" in page, (
            "the page and the fragment disagree about which teams are listed"
        )


#: Panel fragments that draw rows, and the container they draw them into. A
#: panel with an empty container and a caption under it is a blank panel, which
#: is the one thing this app is not allowed to render: the rule is that every
#: panel shows either real content or a styled empty state that says why.
ROW_CONTAINERS = {
    "shape": "shape-rows", "grid": "matrix", "seeds": "seed-rows",
    "gauntlet": "gaunt-rows", "clock": "clock-rows", "ledger": "ledger",
    "swap": "matrix", "volatility": "scatter", "regret": "rows",
    "trouble": "rows", "odds": "rows", "allplay": "rows",
}


@pytest.mark.parametrize("name,container", sorted(ROW_CONTAINERS.items()))
def test_a_panel_is_never_an_empty_container(client, app, name, container, no_network):
    """Found on the live league, not in the demo.

    The gauntlet reported itself available because there were fourteen fixtures
    still to come, and drew nothing, because with no settled weeks there was no
    record to measure any of those opponents by. The result was an empty box
    with an explanatory caption under it, which reads as a bug and is one. So
    `available` has to mean "there is something to draw", never "the feed
    answered".
    """
    body = client.get(f"/partials/panel/{name}").get_data(as_text=True)
    if "empty" in body and "<strong>" in body:
        return                      # a proper empty state, which is the point
    assert container in body, f"{name} drew neither rows nor an empty state"
    after = body.split(container, 1)[1]
    # Something has to follow the container's opening tag before it closes.
    inner = after.split(">", 1)[1].split("</div>")[0].split("</table>")[0]
    assert inner.strip(), (
        f"the {name} panel rendered an empty {container} container: it is "
        f"reporting itself available with nothing to draw"
    )

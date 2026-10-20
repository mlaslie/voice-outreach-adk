"""The cards' primaryColor follows Admin → Branding (like the time format)."""

import asyncio

import pytest
from voa_nurse import cards, worklists
from voa_nurse.backend import BackendError


@pytest.fixture(autouse=True)
def _reset():
    saved = cards.BRAND_DEFAULT
    cards.BRAND_DEFAULT = "#22873B"
    cards.set_brand(None)
    worklists._brand_checked_at = -worklists._FORMAT_TTL_S
    yield
    cards.BRAND_DEFAULT = saved
    cards.set_brand(None)
    worklists._brand_checked_at = -worklists._FORMAT_TTL_S


class _Api:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def get_branding(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _again():
    worklists._brand_checked_at = -worklists._FORMAT_TTL_S


def test_brand_follows_branding_at_most_once_a_minute():
    api = _Api({"colors": {"brand": "#1a5fb4"}})
    asyncio.run(worklists.refresh_brand(api))
    assert cards.BRAND == "#1a5fb4"
    api.result = {"colors": {"brand": "#000000"}}
    asyncio.run(worklists.refresh_brand(api))
    assert api.calls == 1 and cards.BRAND == "#1a5fb4"  # cached


def test_reset_returns_to_the_configured_colour():
    asyncio.run(worklists.refresh_brand(_Api({"colors": {"brand": "#1a5fb4"}})))
    _again()
    asyncio.run(worklists.refresh_brand(_Api({"colors": {}})))
    assert cards.BRAND == "#22873B"


def test_errors_and_bad_values_are_safe():
    asyncio.run(worklists.refresh_brand(_Api({"colors": {"brand": "#1a5fb4"}})))
    _again()
    asyncio.run(worklists.refresh_brand(_Api(BackendError("down"))))
    assert cards.BRAND == "#1a5fb4"  # last known colour stays
    _again()
    asyncio.run(worklists.refresh_brand(_Api({"colors": {"brand": "red; x"}})))
    assert cards.BRAND == "#22873B"


def test_the_card_theme_uses_the_brand():
    cards.set_brand("#1a5fb4")
    msgs = cards.result_card("Done", ["Saved."]).messages()
    assert msgs[0]["createSurface"]["theme"] == {"primaryColor": "#1a5fb4"}

"""Tests for the HomeSejour client (parsing and login flow)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.homesejour.client import (
    HomeSejourClient,
    InvalidAuth,
    _parse_amount,
    _parse_auth_form,
    _parse_check_code,
    normalize_totp_secret,
    parse_plannings,
)

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "JBSWY3DPEHPK3PXP"
TOKEN_URL = "https://homesejour.fr/token/ZmFrZS10b2tlbg"

AUTH_PAGE = """
<form name="AUTH" action="/authhomesejour/Auth/STEP1" method="post">
  <input type="hidden" name="A13" value="0">
  <input TYPE="text" NAME="A3" VALUE="">
  <input TYPE="password" NAME="A5" VALUE="">
  <input TYPE="text" NAME="A43" VALUE="https://homesejour.fr/" readonly>
  <input TYPE="text" NAME="A14" VALUE="2010010112305900_" readonly>
  <input TYPE="text" NAME="A45" VALUE="sessid123" readonly>
  <input type="button" name="A6" value="Connexion">
  <input type="hidden" name="WD_ACTION_" value="">
</form>
"""
CODE_PAGE = '<form name="AUTH" action="/authhomesejour/Auth/STEP2" method="post"></form>'


def check_code_answer(value: int, url: str | None = TOKEN_URL) -> str:
    champ = f'<CHAMP ALIAS="A47" TYPE="2"><PROP NUM="21">{url}</PROP></CHAMP>' if url else ""
    return (
        '<?xml version="1.0" encoding="utf-8" ?><WAJAX>'
        f'{champ}<RESULTAT FORMAT="JSON"><![CDATA[{{ "type":{{ "wl":8 }}, "value":{value} }}]]></RESULTAT></WAJAX>'
    )


# ---------------------------------------------------------------- parsing


def test_parse_plannings() -> None:
    props = parse_plannings((FIXTURES / "plannings.html").read_text(encoding="utf-8"))
    assert [p.id for p in props] == ["74", "81"]

    first = props[0]
    assert first.name == "Résidence EXEMPLE"
    assert first.reference == "EXEMPLE"
    assert first.location == "LA COUARDE SUR MER (17670)"
    assert [b.state for b in first.bookings] == ["owner", "resa", "closed", "cancelled"]

    owner = first.bookings[0]
    assert owner.id == "2058"
    assert owner.reference == "CT02058"
    assert owner.origin == "Agence"
    assert owner.added == date(2026, 10, 23)
    assert (owner.start, owner.end) == (date(2027, 7, 24), date(2027, 8, 21))
    assert owner.nights == 28
    assert owner.owner_net == 0.0
    assert first.bookings[1].owner_net == 1234.56

    assert props[1].name == "Villa SECONDE"
    assert props[1].bookings == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.00 €", 0.0), ("1 234.56 €", 1234.56), ("1\xa0234,56 €", 1234.56), ("1,234.56€", 1234.56), ("", None)],
)
def test_parse_amount(raw: str, expected: float | None) -> None:
    assert _parse_amount(raw) == expected


def test_parse_auth_form() -> None:
    action, fields = _parse_auth_form(AUTH_PAGE)
    assert action == "/authhomesejour/Auth/STEP1"
    assert fields == {
        "A13": "0",
        "A3": "",
        "A5": "",
        "A43": "https://homesejour.fr/",
        "A14": "2010010112305900_",
        "A45": "sessid123",
    }


def test_parse_check_code() -> None:
    assert _parse_check_code(check_code_answer(1)) == TOKEN_URL
    with pytest.raises(InvalidAuth):
        _parse_check_code(check_code_answer(0, None))


def test_normalize_totp_secret() -> None:
    assert normalize_totp_secret("jbsw y3dp ehpk 3pxp") == SECRET
    with pytest.raises(ValueError):
        normalize_totp_secret("not base32 !!")


# ---------------------------------------------------------------- login flow


class FakeHomeSejour:
    """Minimal fake of homesejour.fr + auth.homesejour.fr served from one local server."""

    def __init__(self, check_value: int = 1) -> None:
        self.check_value = check_value
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.logged_in = False
        self.app = web.Application()
        r = self.app.router
        r.add_get("/", self.home)
        r.add_get("/authhomesejour", self.auth_page)
        r.add_post("/authhomesejour/Auth/STEP1", self.step1)
        r.add_post("/authhomesejour/Auth/STEP2", self.step2)
        r.add_get("/token/{token}", self.token)
        r.add_get("/login", self.login_page)
        r.add_post("/login/agence", self.agency)
        r.add_get("/activite/plannings", self.plannings)

    async def _record(self, request: web.Request) -> dict[str, str]:
        data = dict(await request.post()) if request.method == "POST" else dict(request.query)
        self.calls.append((request.path, data))
        return data

    async def home(self, request: web.Request) -> web.Response:
        await self._record(request)
        resp = web.Response(text="home")
        resp.set_cookie("hsejsessid", "sessid123")
        return resp

    async def auth_page(self, request: web.Request) -> web.Response:
        assert (await self._record(request))["HSEJSESSID"] == "sessid123"
        return web.Response(text=AUTH_PAGE, content_type="text/html")

    async def step1(self, request: web.Request) -> web.Response:
        await self._record(request)
        return web.Response(text=CODE_PAGE, content_type="text/html")

    async def step2(self, request: web.Request) -> web.Response:
        await self._record(request)
        url = f"{self.base}/token/abc" if self.check_value == 1 else None
        return web.Response(text=check_code_answer(self.check_value, url), content_type="text/xml")

    async def token(self, request: web.Request) -> web.Response:
        await self._record(request)
        resp = web.Response(text="ok")
        resp.set_cookie("AUTHSPILSTOKEN", "tok")
        return resp

    async def login_page(self, request: web.Request) -> web.Response:
        await self._record(request)
        return web.Response(
            text='<select name="agence-select"><option value="-">-</option>'
            '<option value="161700005_62">X</option></select>',
            content_type="text/html",
        )

    async def agency(self, request: web.Request) -> web.Response:
        await self._record(request)
        self.logged_in = True
        return web.Response(text="ok")

    async def plannings(self, request: web.Request) -> web.Response:
        await self._record(request)
        if not self.logged_in or request.cookies.get("AUTHSPILSTOKEN") != "tok":
            raise web.HTTPFound("/login")
        return web.Response(
            text=(FIXTURES / "plannings.html").read_text(encoding="utf-8"), content_type="text/html"
        )

    def calls_to(self, path: str) -> list[dict[str, str]]:
        return [data for p, data in self.calls if p == path]


@pytest.fixture
async def fake(socket_enabled):
    fake = FakeHomeSejour()
    server = TestServer(fake.app)
    await server.start_server()
    fake.base = str(server.make_url("")).rstrip("/")
    yield fake
    await server.close()


def make_client(fake: FakeHomeSejour, session: aiohttp.ClientSession, password: str = "pw"):
    return HomeSejourClient(
        session,
        "owner@example.com",
        password,
        SECRET,
        base_url=fake.base,
        auth_url=fake.base + "/authhomesejour",
    )


async def test_login_and_fetch(fake: FakeHomeSejour) -> None:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        props = await make_client(fake, session).async_get_properties()

    assert [p.id for p in props] == ["74", "81"]
    step1 = fake.calls_to("/authhomesejour/Auth/STEP1")[0]
    assert step1["A3"] == "owner@example.com"
    assert step1["A5"] == "pw"
    assert step1["A45"] == "sessid123"
    assert step1["A14"] == "2010010112305900_"
    assert step1["WD_BUTTON_CLICK_"] == "A6"
    step2 = fake.calls_to("/authhomesejour/Auth/STEP2")[0]
    assert step2["EXECUTEPROCCHAMPS"] == "Auth.CheckCode"
    assert step2["PA1"] == "owner@example.com"
    assert re.fullmatch(r"\d{6}", step2["PA2"])
    assert fake.calls_to("/login/agence") == [{"agence-select": "161700005_62"}]


async def test_login_rejected(fake: FakeHomeSejour) -> None:
    fake.check_value = 0
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        with pytest.raises(InvalidAuth):
            await make_client(fake, session, password="bad").async_login()


async def test_relogin_when_session_expired(fake: FakeHomeSejour) -> None:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        client = make_client(fake, session)
        await client.async_get_properties()
        fake.logged_in = False  # server-side session expiry
        props = await client.async_get_properties()

    assert len(props) == 2
    assert len(fake.calls_to("/login/agence")) == 2

"""Async client for the HomeSejour owner portal (https://homesejour.fr).

HomeSejour has no public API. This client replays what the browser does:

1. ``GET https://homesejour.fr/`` sets the ``hsejsessid`` cookie.
2. ``GET https://auth.homesejour.fr/authhomesejour?HSEJSESSID=<cookie>&URL_RETURN=homesejour.fr``
   returns a WINDEV form (``AUTH``). Its fields are posted back with ``A3`` = email
   and ``A5`` = password (button ``A6``).
3. An AJAX call ``Auth.CheckCode`` posts the email and the current TOTP code and
   answers with a one-time ``https://homesejour.fr/token/...`` URL.
4. Visiting that URL sets the ``AUTHSPILSTOKEN`` / ``hsejsessag`` session cookies.
5. ``POST /login/agence`` selects the agency (owner account) listed on ``/login``.
6. ``GET /activite/plannings`` is a server-rendered page listing, for each property,
   every booking ("dossier") in a table. The whole history is in that one page.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp
import pyotp
from bs4 import BeautifulSoup, Tag

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://homesejour.fr"
AUTH_URL = "https://auth.homesejour.fr/authhomesejour"
PLANNINGS_PATH = "/activite/plannings"
SESSION_COOKIE = "hsejsessid"
TIMEOUT = aiohttp.ClientTimeout(total=60)

# Booking states, from the CSS class of the "État" cell (see the page legend).
STATE_OWNER = "owner"  # Réservation personnelle
STATE_RESERVED = "resa"  # La propriété est réservée
STATE_OPTION = "option"  # Une option est posée
STATE_LOCKED = "locked"  # Période réservée à ma gestion
STATE_TOUR_OPERATOR = "to"  # Période fournie à un TO
STATE_CLOSED = "closed"  # Ce dossier est clôturé
STATE_CANCELLED = "cancelled"  # Ce dossier est annulé
STATE_UNAVAILABLE = "nc"  # Information indisponible
KNOWN_STATES = {
    STATE_OWNER,
    STATE_RESERVED,
    STATE_OPTION,
    STATE_LOCKED,
    STATE_TOUR_OPERATOR,
    STATE_CLOSED,
    STATE_CANCELLED,
    STATE_UNAVAILABLE,
}


class HomeSejourError(Exception):
    """Base error for the HomeSejour client."""


class CannotConnect(HomeSejourError):
    """The portal could not be reached or answered unexpectedly."""


class InvalidAuth(HomeSejourError):
    """Username, password or TOTP secret was rejected."""


class SessionExpired(HomeSejourError):
    """The session cookies are no longer valid."""


@dataclass(frozen=True)
class Booking:
    """A booking ("dossier") on a property."""

    id: str
    reference: str
    state: str
    start: date
    end: date
    origin: str | None = None
    added: date | None = None
    owner_net: float | None = None

    @property
    def nights(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True)
class Property:
    """A property managed by the agency for the owner."""

    id: str
    name: str
    reference: str | None = None
    location: str | None = None
    bookings: tuple[Booking, ...] = field(default_factory=tuple)


def normalize_totp_secret(secret: str) -> str:
    """Return the base32 secret without spaces, upper-cased. Raise ValueError if invalid."""
    cleaned = re.sub(r"[\s-]", "", secret).upper()
    try:
        pyotp.TOTP(cleaned).now()
    except Exception as err:  # binascii.Error / ValueError depending on input
        raise ValueError("invalid TOTP secret") from err
    return cleaned


class HomeSejourClient:
    """Log in to homesejour.fr and read the owner's bookings."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        totp_secret: str,
        *,
        base_url: str = BASE_URL,
        auth_url: str = AUTH_URL,
    ) -> None:
        # The session must have its own cookie jar (HA: async_create_clientsession).
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._auth_url = auth_url
        self._username = username.strip()
        self._password = password
        self._totp = pyotp.TOTP(normalize_totp_secret(totp_secret))
        self._logged_in = False

    # ------------------------------------------------------------------ auth

    async def async_login(self) -> None:
        """Run the full login flow. Raise InvalidAuth or CannotConnect."""
        self._session.cookie_jar.clear()
        self._logged_in = False

        await self._get(self._base_url + "/")
        sessid = self._cookie(SESSION_COOKIE)
        if not sessid:
            raise CannotConnect("homesejour.fr did not set a session cookie")

        auth_page = await self._get(
            self._auth_url, params={"HSEJSESSID": sessid, "URL_RETURN": "homesejour.fr"}
        )
        action, fields = _parse_auth_form(auth_page)
        fields.update(
            {
                "WD_JSON_PROPRIETE_": json.dumps(
                    {
                        "m_oProprietesSecurisees": {},
                        "m_oChampsModifies": {"A3": True, "A5": True},
                        "m_oVariablesProjet": {},
                        "m_oVariablesPage": {},
                    },
                    separators=(",", ":"),
                ),
                "WD_BUTTON_CLICK_": "A6",
                "WD_ACTION_": "",
                "A3": self._username,
                "A5": self._password,
            }
        )
        code_page = await self._post(urljoin(self._auth_url, action), data=fields)
        code_action, _ = _parse_auth_form(code_page)

        answer = await self._post(
            urljoin(self._auth_url, code_action),
            data={
                "WD_ACTION_": "AJAXEXECUTE",
                "EXECUTEPROCCHAMPS": "Auth.CheckCode",
                "WD_CONTEXTE_": "A12",
                "PA1": self._username,
                "PA2": self._totp.now(),
                "PA3": "1",
            },
        )
        token_url = _parse_check_code(answer)
        if not token_url.startswith(self._base_url + "/token/"):
            raise CannotConnect("unexpected token URL host")

        await self._get(token_url)
        await self._select_agency()
        self._logged_in = True

    async def _select_agency(self) -> None:
        page = await self._get(self._base_url + "/login")
        soup = BeautifulSoup(page, "html.parser")
        select = soup.find("select", attrs={"name": "agence-select"})
        if select is None:
            return  # already bound to a single agency
        values = [
            opt.get("value", "").strip()
            for opt in select.find_all("option")
            if opt.get("value", "").strip() not in ("", "-")
        ]
        if not values:
            raise CannotConnect("no agency available for this account")
        if len(values) > 1:
            _LOGGER.warning("Several agencies available, using the first one")
        await self._post(self._base_url + "/login/agence", data={"agence-select": values[0]})

    # ------------------------------------------------------------------ data

    async def async_get_properties(self) -> list[Property]:
        """Return every property with all its bookings. Log in again if needed."""
        if not self._logged_in:
            await self.async_login()
        try:
            return parse_plannings(await self._get_plannings())
        except SessionExpired:
            _LOGGER.debug("HomeSejour session expired, logging in again")
            await self.async_login()
            return parse_plannings(await self._get_plannings())

    async def _get_plannings(self) -> str:
        try:
            async with self._session.get(
                self._base_url + PLANNINGS_PATH, allow_redirects=False, timeout=TIMEOUT
            ) as resp:
                if resp.status in (301, 302, 303, 307, 401, 403):
                    self._logged_in = False
                    raise SessionExpired
                if resp.status != 200:
                    raise CannotConnect(f"plannings returned HTTP {resp.status}")
                page = await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnect(str(err)) from err
        if "planning-content" not in page:
            self._logged_in = False
            raise SessionExpired
        return page

    # ------------------------------------------------------------------ http

    def _cookie(self, name: str) -> str | None:
        for cookie in self._session.cookie_jar:
            if cookie.key == name:
                return cookie.value
        return None

    async def _get(self, url: str, **kwargs: Any) -> str:
        try:
            async with self._session.get(url, timeout=TIMEOUT, **kwargs) as resp:
                resp.raise_for_status()
                return await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnect(str(err)) from err

    async def _post(self, url: str, **kwargs: Any) -> str:
        try:
            async with self._session.post(url, timeout=TIMEOUT, **kwargs) as resp:
                resp.raise_for_status()
                return await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnect(str(err)) from err


# ---------------------------------------------------------------------- parsing


def _parse_auth_form(page: str) -> tuple[str, dict[str, str]]:
    """Return the AUTH form action and its current field values."""
    soup = BeautifulSoup(page, "html.parser")
    form = soup.find("form", attrs={"name": "AUTH"})
    if form is None or not form.get("action"):
        raise CannotConnect("login form not found")
    fields: dict[str, str] = {}
    for inp in form.find_all(["input", "textarea"]):
        name = inp.get("name")
        if not name or not re.fullmatch(r"A\d+", name):
            continue
        if inp.get("type", "").lower() in ("button", "submit", "image", "checkbox", "radio"):
            continue
        fields[name] = inp.get("value", "") if inp.name == "input" else inp.get_text()
    return form["action"], fields


def _parse_check_code(xml_text: str) -> str:
    """Return the token URL from an ``Auth.CheckCode`` answer, or raise InvalidAuth."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as err:
        raise CannotConnect("unexpected answer to Auth.CheckCode") from err
    result = root.find("RESULTAT")
    try:
        value = json.loads(result.text or "{}").get("value") if result is not None else None
    except json.JSONDecodeError:
        value = None
    token_url = None
    for champ in root.iter("CHAMP"):
        for prop in champ.iter("PROP"):
            if (prop.text or "").startswith("http"):
                token_url = prop.text.strip()
    if value != 1 or not token_url:
        raise InvalidAuth("credentials or TOTP code rejected")
    return token_url


def _text(node: Tag | None) -> str:
    return " ".join(node.get_text(" ").split()) if node is not None else ""


def _parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_amount(value: str) -> float | None:
    cleaned = re.sub(r"[^\d,.\-]", "", value)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")  # 1,234.56
    else:
        cleaned = cleaned.replace(",", ".")  # 1234,56
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_plannings(page: str) -> list[Property]:
    """Parse ``/activite/plannings`` into properties and bookings."""
    soup = BeautifulSoup(page, "html.parser")
    properties: list[Property] = []
    for pane in soup.select("div.planning-content[id]"):
        heading = pane.find("h3")
        small = heading.find("small") if heading else None
        subtitle = _text(small)
        if small is not None:
            small.extract()
        icon = heading.find("i") if heading else None
        if icon is not None:
            icon.extract()
        name = _text(heading) or pane["id"]

        location = reference = None
        m = re.match(r"situé à (.*?)\s*(?:—\s*Référence\s*(.*))?$", subtitle)
        if m:
            location = m.group(1) or None
            reference = m.group(2) or None

        bookings: list[Booking] = []
        table = pane.select_one("div.desk-only table.table-planning")
        for row in table.select("tbody tr") if table else []:
            cells = row.find_all("td")
            if len(cells) < 7:
                continue
            state_div = cells[1].select_one(".state-case div")
            state = (state_div.get("class") or [""])[0] if state_div else ""
            start = _parse_date(_text(cells[4]))
            end = _parse_date(_text(cells[5]))
            if start is None or end is None:
                continue
            reference_text = _text(cells[0])
            bookings.append(
                Booking(
                    id=cells[0].get("data-order") or re.sub(r"\D", "", reference_text),
                    reference=reference_text,
                    state=state,
                    origin=_text(cells[2]) or None,
                    added=_parse_date(_text(cells[3])),
                    start=start,
                    end=end,
                    owner_net=_parse_amount(_text(cells[6])),
                )
            )
            if state not in KNOWN_STATES:
                _LOGGER.warning("Unknown HomeSejour booking state %r on %s", state, reference_text)
        properties.append(
            Property(
                id=pane["id"],
                name=name,
                reference=reference,
                location=location,
                bookings=tuple(bookings),
            )
        )
    if not properties:
        raise HomeSejourError("no property found on the plannings page")
    return properties

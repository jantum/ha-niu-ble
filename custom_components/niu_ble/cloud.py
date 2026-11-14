"""Lesender Zugriff auf die NIU-Cloud: Login und Fahrtenliste.

Endpunkte und Login-Ablauf nach dem Projekt niu-kqi von BaesTheorem (MIT, siehe NOTICE.md).
Es werden ausschließlich Daten abgerufen.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import aiohttp

ACCOUNT_HOST = "https://account-fk.niu.com/"
API_HOST = "https://app-api-fk.niu.com/"
APP_IDS = ("niu_8xt1afu6", "niu_ktdrr960")
USER_AGENT = (
    "manager/5.12.2 (android; Pixel 8 14);lang=en-US;clientIdentifier=Overseas;"
    "timezone=Europe/Berlin;model=google_Pixel 8;deviceName=Pixel 8;ostype=android"
)
# 1131 = Zugangsdaten/Token ungültig, 20017 = Refresh-Token ungültig
AUTH_STATUSES = {1131, 20017}
PAGE_SIZE = 20
MAX_PAGES = 50
TIMEOUT = aiohttp.ClientTimeout(total=30)


class CloudError(Exception):
    """Fehler beim Cloud-Abruf."""


class CloudAuthError(CloudError):
    """Login abgelehnt."""


def password_hash(password: str) -> str:
    """Die App sendet md5(Passwort); nur dieser Wert wird gespeichert."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()  # noqa: S324


class NiuCloud:
    """Kleine Cloud-Sitzung mit automatischem Neu-Login."""

    def __init__(self, session: aiohttp.ClientSession, account: str, pw_hash: str) -> None:
        self._session = session
        self._account = account
        self._pw_hash = pw_hash
        self._token: str | None = None
        self._expires_at = 0.0

    async def _request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        form: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        headers = {"User-Agent": USER_AGENT, "X-No-Encrypt": "1", "Accept": "application/json"}
        if token:
            headers["token"] = token
        try:
            async with self._session.request(
                method, url, headers=headers, data=form, json=json, params=params, timeout=TIMEOUT
            ) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CloudError(f"NIU-Cloud nicht erreichbar: {err}") from err
        if not isinstance(body, dict):
            raise CloudError("unerwartete Antwort der NIU-Cloud")
        status = body.get("status", 0)
        if status != 0:
            cls = CloudAuthError if status in AUTH_STATUSES else CloudError
            raise cls(f"NIU meldet: {body.get('desc') or status} (Status {status})")
        return body.get("data")

    async def login(self) -> None:
        """Mit Konto und Passwort-Hash anmelden (beide bekannten App-IDs versuchen)."""
        last: CloudError | None = None
        for app_id in APP_IDS:
            form = {
                "account": self._account,
                "password": self._pw_hash,
                "grant_type": "password",
                "scope": "base",
                "app_id": app_id,
            }
            try:
                data = await self._request("POST", ACCOUNT_HOST + "v3/api/oauth2/token", form=form)
            except CloudError as err:
                last = err
                continue
            token = ((data or {}).get("token") or {})
            if token.get("access_token"):
                self._token = token["access_token"]
                self._expires_at = time.time() + int(token.get("token_expires_in") or 0)
                return
            last = CloudError("Login-Antwort ohne Token")
        raise last or CloudError("Login fehlgeschlagen")

    async def _authed(self, method: str, path: str, **kwargs: Any) -> Any:
        """Aufruf mit Token; bei abgelaufenem Token einmal neu anmelden."""
        if not self._token or (self._expires_at and self._expires_at < time.time() + 300):
            await self.login()
        try:
            return await self._request(method, API_HOST + path, token=self._token, **kwargs)
        except CloudAuthError:
            await self.login()
            return await self._request(method, API_HOST + path, token=self._token, **kwargs)

    async def find_serial(self, mac: str) -> str | None:
        """Seriennummer des Rollers mit dieser Bluetooth-MAC im Konto suchen."""
        data = await self._authed("GET", "v5/scooter/list")
        if isinstance(data, dict):
            data = data.get("items") or data.get("list") or []
        serials = [item["sn_id"] for item in data or [] if item.get("sn_id")]
        wanted = mac.replace(":", "").upper()
        for serial in serials:
            try:
                info = await self._authed("GET", "v5/ble/bleinfo", params={"sn": serial}) or {}
            except CloudAuthError:
                raise
            except CloudError:
                continue
            if str(info.get("bleMac", "")).replace(":", "").upper() == wanted:
                return serial
        return serials[0] if len(serials) == 1 else None

    async def rides_page(self, serial: str, page: int) -> list[dict[str, Any]]:
        """Eine Seite der Fahrtenliste (Seite 1 = neueste)."""
        # Der Server verlangt index als String und pagesize als Zahl.
        data = await self._authed(
            "POST",
            "v5/track/list/v3",
            json={"sn": serial, "pagesize": PAGE_SIZE, "index": str(page)},
        )
        items = (data or {}).get("items") if isinstance(data, dict) else None
        return [item for item in items or [] if isinstance(item, dict)]

    async def new_rides(self, serial: str, known: set[str]) -> list[dict[str, Any]]:
        """Seiten abrufen, bis eine Seite nichts Neues mehr enthält."""
        fresh: list[dict[str, Any]] = []
        seen = set(known)
        for page in range(1, MAX_PAGES + 1):
            batch = await self.rides_page(serial, page)
            new = [r for r in batch if str(r.get("trackId")) not in seen]
            if not new:
                break
            seen.update(str(r.get("trackId")) for r in new)
            fresh.extend(new)
        return fresh

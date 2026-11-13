"""BLE-Sitzung zum NIU KQi: verbinden, authentifizieren, Felder lesen (nur lesend)."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import protocol as P
from .const import RESPONSE_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class NiuSession:
    """Eine authentifizierte Verbindung zum Roller."""

    def __init__(self, client: BleakClient, password: str, aes: str) -> None:
        self._client = client
        self._password = password
        self._key = aes if len(aes) in (16, 32) else ""
        self._rx: asyncio.Queue[bytes] = asyncio.Queue()

    def _on_notify(self, _char, data: bytearray) -> None:
        raw = bytes(data)
        if len(raw) % P.FRAME_LEN == 0:
            for i in range(0, len(raw), P.FRAME_LEN):
                self._rx.put_nowait(raw[i : i + P.FRAME_LEN])
        else:
            self._rx.put_nowait(raw)

    async def start(self) -> None:
        """Notifications abonnieren und Passwort-Handshake durchführen."""
        await self._client.start_notify(P.NOTIFY_UUID, self._on_notify)
        frame, rnd = P.handshake_step1(self._password)
        await self._send([frame])
        reply = P.handshake_step1_parse(await self._next(P.HS1_REPLY_HEADERS), self._password)
        await self._send([P.handshake_step2(rnd, reply, self._password)])
        P.handshake_step2_parse(await self._next(P.HS2_REPLY_HEADERS), self._password)

    async def _send(self, frames: list[bytes]) -> None:
        while not self._rx.empty():
            self._rx.get_nowait()
        for frame in frames:
            await self._client.write_gatt_char(P.WRITE_UUID, frame, response=True)

    async def _next(self, headers: tuple[bytes, ...]) -> bytes:
        """Nächster Frame mit passendem Header; unaufgeforderte Frames werden verworfen."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESPONSE_TIMEOUT
        while True:
            left = deadline - loop.time()
            if left <= 0:
                raise TimeoutError("keine Antwort vom Roller")
            async with asyncio.timeout(left):
                frame = await self._rx.get()
            if len(frame) == P.FRAME_LEN and frame[:2] in headers:
                return frame

    async def read(self, names: list[str]) -> dict[str, int | str]:
        """Felder in einem Request lesen."""
        await self._send(P.build_read(names, self._key))
        frames: list[bytes] = []
        while True:
            frame = await self._next(P.READ_REPLY_HEADERS)
            frames.append(frame)
            if P.is_last_frame(frame):
                break
        return P.parse_read(frames, names, self._key)

    async def read_tolerant(self, names: list[str]) -> dict[str, int | str]:
        """Gruppe lesen; antwortet sie nicht, die Felder einzeln versuchen."""
        try:
            return await self.read(names)
        except (P.NiuError, TimeoutError) as err:
            _LOGGER.debug("Gruppe %s fehlgeschlagen (%s), lese einzeln", names, err)
        result: dict[str, int | str] = {}
        for name in names:
            try:
                result.update(await self.read([name]))
            except (P.NiuError, TimeoutError) as err:
                _LOGGER.debug("Feld %s nicht lesbar: %s", name, err)
        return result


async def async_read_scooter(
    device: BLEDevice, password: str, aes: str, groups: list[list[str]]
) -> dict[str, int | str]:
    """Verbinden, Gruppen lesen, trennen."""
    client = await establish_connection(
        BleakClientWithServiceCache, device, device.name or device.address, max_attempts=3
    )
    try:
        session = NiuSession(client, password, aes)
        await session.start()
        values: dict[str, int | str] = {}
        for group in groups:
            values.update(await session.read_tolerant(group))
        return values
    finally:
        await client.disconnect()

"""NIU KQi Bluetooth-LE-Protokoll (BLE-Version 10), nur Lesezugriff.

Abgeleitet aus dem Projekt niu-kqi von BaesTheorem (MIT-Lizenz, siehe NOTICE.md).
Dieses Modul macht kein I/O und enthält bewusst KEINE Schreib-/Steuerbefehle.

Frame (immer 20 Byte):
    Header (2) | Index (1, Anzahl noch folgender Frames) | AES-128-ECB-Block (16) | Summe (1)
"""

from __future__ import annotations

import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SERVICE_UUID = "8ec94e30-f315-4f60-9fb8-838830daea50"
NOTIFY_UUID = "8ec94e31-f315-4f60-9fb8-838830daea50"
WRITE_UUID = "8ec94e32-f315-4f60-9fb8-838830daea50"

FRAME_LEN = 20
BLOCK_LEN = 16

HDR_READ_FIRST = bytes.fromhex("0121")
HDR_READ_NEXT = bytes.fromhex("0101")
HDR_READ_ACK_FIRST = bytes.fromhex("01a1")
HDR_READ_ACK_NEXT = bytes.fromhex("0181")
HDR_READ_ERR = (bytes.fromhex("01e1"), bytes.fromhex("01c1"))
READ_REPLY_HEADERS = (HDR_READ_ACK_FIRST, HDR_READ_ACK_NEXT, *HDR_READ_ERR)

HDR_HS1 = bytes.fromhex("012301")
HDR_HS1_ACK = bytes.fromhex("01a3")
HDR_HS2 = bytes.fromhex("010300")
HDR_HS2_ACK = bytes.fromhex("0183")
HDR_HS_ERR = bytes.fromhex("01c3")
HS1_REPLY_HEADERS = (HDR_HS1_ACK, HDR_HS_ERR)
HS2_REPLY_HEADERS = (HDR_HS2_ACK, HDR_HS_ERR)

# Feldname -> (3-Byte-Code, Länge in Byte, Typ)
FIELDS: dict[str, tuple[str, int, str]] = {
    "bms_soc_rt": ("31001C", 1, "U8"),
    "bms_bat_sta_rt": ("31001D", 2, "U16"),
    "bms_bat_to_cap": ("310017", 2, "U16"),
    "bms_c_cont": ("310018", 2, "U16"),
    "bms_s_ver_n": ("31003C", 8, "UTF-8"),
    "db_k_f_code": ("110006", 1, "U8"),
    "db_k_estimated_mileage": ("11000D", 2, "U16"),
    "db_k_sw_ver": ("110002", 8, "UTF-8"),
    "foc_k_gears": ("210009", 1, "U8"),
    "foc_k_max_speed": ("21003B", 2, "U16"),
    "foc_k_realtime_status1": ("210010", 4, "U32"),
    "foc_k_s_ver": ("210002", 8, "UTF-8"),
    "ecu_bt_ver": ("440000", 8, "UTF-8"),
}

# Ein Request-Block (16 Byte) fasst maximal fünf 3-Byte-Codes.
MAX_FIELDS_PER_READ = 5


class NiuError(Exception):
    """Protokollfehler."""


class NiuAuthError(NiuError):
    """Der Roller hat das BLE-Passwort abgelehnt."""


def checksum(data: bytes) -> int:
    """Summe aller Bytes modulo 256."""
    return sum(data) % 256


def checksum_ok(frame: bytes) -> bool:
    """Prüft das letzte Byte eines Frames."""
    return len(frame) >= 2 and frame[-1] == checksum(frame[:-1])


def aes_key(key: str) -> bytes:
    """16 Zeichen (ASCII) oder 32 Hex-Zeichen -> 16 Byte Schlüssel."""
    if len(key) == 16:
        return key.encode("utf-8")
    if len(key) == 32:
        try:
            return bytes.fromhex(key)
        except ValueError as err:
            raise NiuError("AES-Schlüssel mit 32 Zeichen muss hexadezimal sein") from err
    raise NiuError("AES-Schlüssel muss 16 Zeichen oder 32 Hex-Zeichen lang sein")


def _encrypt(block: bytes, key: str) -> bytes:
    if not key:
        return block
    if len(block) != BLOCK_LEN:
        raise NiuError("AES-Block muss 16 Byte lang sein")
    enc = Cipher(algorithms.AES(aes_key(key)), modes.ECB()).encryptor()  # noqa: S305
    return enc.update(block) + enc.finalize()


def _decrypt(block: bytes, key: str) -> bytes:
    if not key:
        return block
    if len(block) != BLOCK_LEN:
        raise NiuError("AES-Block muss 16 Byte lang sein")
    dec = Cipher(algorithms.AES(aes_key(key)), modes.ECB()).decryptor()  # noqa: S305
    return dec.update(block) + dec.finalize()


def _seal(body: bytes) -> bytes:
    return body + bytes([checksum(body)])


def _payload(frame: bytes) -> bytes:
    return frame[3:-1]


# ----------------------------------------------------------------------------
# Handshake (BLE-Version 10)
# ----------------------------------------------------------------------------


def handshake_step1(password: str) -> tuple[bytes, bytes]:
    """Erster Verify-Frame. Liefert (Frame, Zufallswert)."""
    rnd = secrets.token_bytes(BLOCK_LEN)
    return _seal(HDR_HS1 + _encrypt(rnd, password)), rnd


def handshake_step1_parse(frame: bytes, password: str) -> bytes:
    """Antwort auf Schritt 1 entschlüsseln."""
    header = frame[:2]
    if header == HDR_HS_ERR:
        raise NiuAuthError("BLE-Passwort in Schritt 1 abgelehnt")
    if header != HDR_HS1_ACK:
        raise NiuError(f"unerwarteter Header in Verify-Antwort 1: {header.hex()}")
    if not checksum_ok(frame):
        raise NiuError("Prüfsummenfehler in Verify-Antwort 1")
    return _decrypt(_payload(frame), password)


def handshake_step2(rnd: bytes, reply: bytes, password: str) -> bytes:
    """Zweiter Verify-Frame: AES(md5(Zufall ‖ Antwort ‖ Passwort))."""
    digest = hashlib.md5(rnd + reply + password.encode("utf-8")).digest()  # noqa: S324
    return _seal(HDR_HS2 + _encrypt(digest, password))


def handshake_step2_parse(frame: bytes, password: str) -> None:
    """Antwort auf Schritt 2 prüfen."""
    header = frame[:2]
    if header == HDR_HS_ERR:
        raise NiuAuthError("BLE-Passwort in Schritt 2 abgelehnt")
    if header != HDR_HS2_ACK:
        raise NiuError(f"unerwarteter Header in Verify-Antwort 2: {header.hex()}")
    if not checksum_ok(frame):
        raise NiuError("Prüfsummenfehler in Verify-Antwort 2")
    if _decrypt(_payload(frame), password)[0] != 0:
        raise NiuAuthError("BLE-Passwort-Verifikation fehlgeschlagen")


# ----------------------------------------------------------------------------
# Lesen
# ----------------------------------------------------------------------------


def build_read(names: list[str], key: str) -> list[bytes]:
    """Lese-Request für die genannten Felder (ein Frame je 16-Byte-Block)."""
    data = b"".join(bytes.fromhex(FIELDS[name][0]) for name in names)
    count = max(1, (len(data) + BLOCK_LEN - 1) // BLOCK_LEN)
    frames = []
    for i in range(count):
        chunk = data[i * BLOCK_LEN : (i + 1) * BLOCK_LEN].ljust(BLOCK_LEN, b"\x00")
        header = HDR_READ_FIRST if i == 0 else HDR_READ_NEXT
        frames.append(_seal(header + bytes([count - i - 1]) + _encrypt(chunk, key)))
    return frames


def is_last_frame(frame: bytes) -> bool:
    """Index-Byte 0 schließt eine Antwort ab; Fehler-Frames stehen allein."""
    return frame[2] == 0 or frame[:2] in HDR_READ_ERR


def _decode(typ: str, raw: bytes) -> int | str:
    if typ in ("U8", "U16", "U32"):
        return int.from_bytes(raw, "big")
    if typ == "UTF-8":
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
    return raw.hex()


def parse_read(frames: list[bytes], names: list[str], key: str) -> dict[str, int | str]:
    """Antwort-Frames zusammensetzen und in Felder zerlegen."""
    expected = actual = 0
    data = b""
    for frame in frames:
        if not checksum_ok(frame):
            raise NiuError(f"Prüfsummenfehler in Antwort-Frame {frame.hex()}")
        header, index = frame[:2], frame[2]
        if index == 0xFF:
            continue
        if header == HDR_READ_ACK_FIRST:
            expected = index + 1
            data += _decrypt(_payload(frame), key)
            actual += 1
        elif header == HDR_READ_ACK_NEXT:
            data += _decrypt(_payload(frame), key)
            actual += 1
        elif header in HDR_READ_ERR:
            code = _decrypt(_payload(frame), key)[0] if key else frame[3]
            raise NiuError(f"Roller hat das Lesen abgelehnt (Fehler {code:02X})")
        else:
            raise NiuError(f"unerwarteter Antwort-Header {header.hex()}")
    if expected != actual:
        raise NiuError(f"Frame-Verlust: {expected} erwartet, {actual} erhalten")

    result: dict[str, int | str] = {}
    pos = 0
    for name in names:
        _code, length, typ = FIELDS[name]
        end = pos + length
        if end > len(data):
            raise NiuError(f"Antwort zu kurz für {name}")
        result[name] = _decode(typ, data[pos:end])
        pos = end
    return result

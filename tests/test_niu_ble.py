"""Eigenständiger Test: simulierter Roller (nach Protokollbeschreibung) gegen protocol.py/client.py."""

import asyncio
import hashlib
import importlib
import json
import pathlib
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

P = importlib.import_module("custom_components.niu_ble.protocol")
C = importlib.import_module("custom_components.niu_ble.client")

PWD = "A1b2C3d4E5f6G7h8"
VALUES = {
    "bms_soc_rt": 63, "bms_bat_sta_rt": 32770, "bms_bat_to_cap": 9822, "bms_c_cont": 35,
    "db_k_f_code": 0, "db_k_estimated_mileage": 0, "foc_k_gears": 2, "foc_k_max_speed": 200,
    "foc_k_realtime_status1": 2177, "db_k_sw_ver": "KBC38V05", "foc_k_s_ver": "KBE38D05",
    "bms_s_ver_n": "K3D35V16", "ecu_bt_ver": "KBQ44V01",
}
BY_CODE = {bytes.fromhex(code): (name, ln, typ) for name, (code, ln, typ) in P.FIELDS.items()}


def enc(block, key):
    if not key:
        return block
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(block) + e.finalize()


def dec(block, key):
    if not key:
        return block
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return d.update(block) + d.finalize()


def seal(body):
    return body + bytes([sum(body) % 256])


class FakeScooter:
    """Roller-Seite, unabhängig von protocol.py implementiert."""

    def __init__(self, password, aes_key, silent=()):
        self.pwd = password.encode()
        self.key = aes_key
        self.silent = set(silent)
        self.notify = None
        self.rnd = self.reply = None
        self.authed = False
        self.pending = b""
        self.writes = []

    async def start_notify(self, uuid, cb):
        assert uuid == P.NOTIFY_UUID
        self.notify = cb

    async def write_gatt_char(self, uuid, data, response=False):
        assert uuid == P.WRITE_UUID and response is True
        frame = bytes(data)
        self.writes.append(frame)
        assert len(frame) == 20 and frame[-1] == sum(frame[:-1]) % 256
        hdr, idx, block = frame[:2], frame[2], frame[3:-1]
        if frame[:3] == bytes.fromhex("012301"):
            self.rnd = dec(block, self.pwd)
            self.reply = bytes(range(16))
            self._push(seal(bytes.fromhex("01a300") + enc(self.reply, self.pwd)))
        elif frame[:3] == bytes.fromhex("010300"):
            want = hashlib.md5(self.rnd + self.reply + self.pwd).digest()
            ok = dec(block, self.pwd) == want
            self.authed = ok
            self._push(seal(bytes.fromhex("018300") + enc(bytes([0 if ok else 1]) + bytes(15), self.pwd)))
        elif hdr in (bytes.fromhex("0121"), bytes.fromhex("0101")):
            assert self.authed
            self.pending += dec(block, self.key)
            if idx == 0:
                self._answer_read(self.pending)
                self.pending = b""
        else:
            raise AssertionError(f"unerwarteter (Schreib-?)Frame {frame.hex()}")

    def _answer_read(self, codes):
        out = b""
        for i in range(0, len(codes), 3):
            code = codes[i:i + 3]
            if code == b"\x00\x00\x00" or len(code) < 3:
                break
            name, ln, typ = BY_CODE[code]
            if name in self.silent:
                return  # wie am echten Roller: ganze Gruppe bleibt unbeantwortet
            v = VALUES[name]
            out += v.to_bytes(ln, "big") if isinstance(v, int) else v.encode().ljust(ln, b"\x00")
        n = max(1, (len(out) + 15) // 16)
        frames = b""
        for i in range(n):
            h = "01a1" if i == 0 else "0181"
            frames += seal(bytes.fromhex(h) + bytes([n - i - 1]) + enc(out[i * 16:(i + 1) * 16].ljust(16, b"\x00"), self.key))
        # ein unaufgeforderter Push davor, Antwort-Frames in einer Notification gebündelt
        self._push(seal(bytes.fromhex("012700") + bytes(16)))
        self._push(frames)

    def _push(self, data):
        asyncio.get_running_loop().call_soon(self.notify, None, bytearray(data))


async def run(aes):
    key = P.aes_key(aes) if aes else b""
    groups = [
        ["bms_soc_rt", "bms_bat_sta_rt", "bms_bat_to_cap", "bms_c_cont", "db_k_f_code"],
        ["db_k_estimated_mileage", "foc_k_gears", "foc_k_max_speed", "foc_k_realtime_status1"],
        ["db_k_sw_ver", "foc_k_s_ver", "bms_s_ver_n", "ecu_bt_ver"],
    ]
    scooter = FakeScooter(PWD, key)
    s = C.NiuSession(scooter, PWD, aes)
    await s.start()
    got = {}
    for g in groups:
        got.update(await s.read_tolerant(g))
    assert got == VALUES, got
    # nur Handshake- und Lese-Frames wurden geschrieben
    assert all(w[:2].hex() in ("0123", "0103", "0121", "0101") for w in scooter.writes)

    # falsches Passwort -> NiuAuthError
    try:
        await C.NiuSession(FakeScooter(PWD, key), "X" * 16, aes).start()
        raise SystemExit("falsches Passwort wurde akzeptiert")
    except P.NiuAuthError:
        pass


async def run_silent_field():
    C.RESPONSE_TIMEOUT = 0.2
    scooter = FakeScooter(PWD, b"0123456789abcdef", silent={"bms_c_cont"})
    s = C.NiuSession(scooter, PWD, "0123456789abcdef")
    await s.start()
    got = await s.read_tolerant(["bms_soc_rt", "bms_bat_sta_rt", "bms_c_cont"])
    assert got == {"bms_soc_rt": 63, "bms_bat_sta_rt": 32770}, got


def main():
    for aes in ("0123456789abcdef", "00112233445566778899aabbccddeeff", ""):
        asyncio.run(run(aes))
    asyncio.run(run_silent_field())
    # bekannter Vektor: unverschlüsselter Read von foc_k_gears
    f = P.build_read(["foc_k_gears"], "")[0]
    assert f.hex() == "012100" + "210009" + "00" * 13 + "%02x" % ((0x01 + 0x21 + 0x21 + 0x09) % 256), f.hex()
    for name in ("de.json", "en.json"):
        json.loads((ROOT / "custom_components/niu_ble/translations" / name).read_text("utf-8"))
    json.loads((ROOT / "custom_components/niu_ble/manifest.json").read_text("utf-8"))
    print("alle Tests ok")


main()

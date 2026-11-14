"""Tests der Fahrten-Aufbereitung (ohne Home Assistant)."""
from datetime import datetime, timedelta, timezone
import importlib.util
import pathlib
import sys
from zoneinfo import ZoneInfo

path = pathlib.Path(__file__).resolve().parents[1] / "custom_components/niu_ble/rides.py"
spec = importlib.util.spec_from_file_location("niu_rides", path)
R = importlib.util.module_from_spec(spec)
sys.modules["niu_rides"] = R
spec.loader.exec_module(R)

TZ = ZoneInfo("Europe/Berlin")
CAP = 9822 * 48 / 1000


def ms(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)


def raw(track="t1", start=ms(2026, 11, 5, 9, 7), minutes=12, meters=3600, used=22, speed=19.9):
    return {"trackId": track, "startTime": start, "endTime": start + minutes * 60000,
            "distance": meters, "ridingtime": minutes * 60 - 10, "avespeed": speed,
            "power_consumption": used, "date": 20261105}


def test_normalize():
    ride = R.normalize(raw(), TZ, CAP)
    # Rohwert 09:07 UTC + 1 h Versatz = 10:07 UTC = 11:07 Ortszeit (wie in der App)
    assert datetime.fromisoformat(ride["start"]).astimezone(TZ).strftime("%d.%m. %H:%M") == "05.11. 11:07"
    assert ride["km"] == 3.6 and ride["verbrauch"] == 22 and ride["tempo"] == 19.9
    assert ride["prozent_pro_km"] == 6.11
    assert ride["wh_pro_km"] == round(6.11 / 100 * CAP, 1)
    assert ride["dauer_min"] == 11.8 and ride["temperatur"] is None


def test_summer_time_offset():
    ride = R.normalize(raw(start=ms(2026, 7, 1, 9, 0)), TZ, None)
    assert datetime.fromisoformat(ride["start"]).astimezone(TZ).hour == 13  # 09 UTC + 2 h -> 11 UTC = 13 MESZ
    assert ride["wh_pro_km"] is None


def test_filters():
    assert R.normalize(raw(used=-5), TZ, CAP) is None                    # Ladevorgang
    assert R.normalize(raw(used=0, minutes=40, meters=100), TZ, CAP) is None
    assert R.normalize({"trackId": "x"}, TZ, CAP) is None                # kaputt
    short = R.normalize(raw(meters=100, used=1, minutes=2), TZ, CAP)
    assert short["km"] == 0.1 and short["prozent_pro_km"] is None
    no_cons = R.normalize({**raw(), "power_consumption": None}, TZ, CAP)
    assert no_cons["verbrauch"] is None and no_cons["prozent_pro_km"] is None


def test_aggregates():
    now = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
    rides = {
        "a": R.normalize(raw("a", ms(2026, 11, 13, 9, 0), meters=4000, used=20), TZ, CAP),
        "b": R.normalize(raw("b", ms(2026, 11, 1, 9, 0), meters=6000, used=18), TZ, CAP),
        "c": R.normalize(raw("c", ms(2026, 9, 1, 9, 0), meters=10000, used=20), TZ, CAP),
        "d": R.normalize({**raw("d", ms(2026, 11, 12, 9, 0)), "power_consumption": None}, TZ, CAP),
    }
    lst = R.sorted_rides(rides)
    assert [r["id"] for r in lst] == ["a", "d", "b", "c"]
    assert R.average_rate(lst, now, 7) == 5.0
    assert R.average_rate(lst, now, 30) == 3.8
    assert R.average_rate(lst, now) == 2.9
    assert R.average_rate([], now) is None
    assert R.total_km(lst) == 23.6

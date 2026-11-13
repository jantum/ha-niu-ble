"""Tests der Fahrtenbuch-Logik (ohne Home Assistant)."""
from datetime import datetime, timedelta, timezone
import importlib.util
import pathlib
import sys

path = pathlib.Path(__file__).resolve().parents[1] / "custom_components/niu_ble/logbook.py"
spec = importlib.util.spec_from_file_location("niu_logbook", path)
L = importlib.util.module_from_spec(spec)
sys.modules["niu_logbook"] = L
spec.loader.exec_module(L)

T0 = datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc)
CAP = 9822 * 48 / 1000


def odo(log, value, t, unsynced=False, in_range=True):
    return log.odometer(value, t, capacity_wh=CAP, rides_unsynced=unsynced, in_range=in_range)


def test_trip_and_segment():
    log = L.TripLog()
    assert odo(log, 384.33, T0) is None            # Startpunkt
    assert log.poll(100, 4.0, None) is None
    log.leave(T0, 100, 4.0)
    back = T0 + timedelta(minutes=95)
    assert odo(log, 390.0, back, in_range=False) is None   # Sync unterwegs: noch nicht abrechnen
    trip = log.poll(80, 6.0, back)
    assert trip == {"start": T0.isoformat(), "ende": back.isoformat(), "dauer_min": 95,
                    "soc_start": 100, "soc_ende": 80, "verbrauch": 20, "temperatur": 5.0}
    assert log.open_trip is None and log.waiting_for_sync
    assert odo(log, 390.0, back, unsynced=True) is None    # Roller meldet: noch nicht alles synchron
    seg = odo(log, 392.33, back + timedelta(hours=1))
    assert seg["km"] == 8.0 and seg["verbrauch"] == 20
    assert seg["prozent_pro_km"] == 2.5
    assert seg["wh_pro_km"] == round(0.2 * CAP / 8, 1)
    assert seg["temperatur"] == 5.0
    assert not log.waiting_for_sync and log.segment["odo_start"] == 392.33


def test_no_consumption_is_no_trip():
    log = L.TripLog()
    log.poll(70, None, None)
    log.leave(T0, 70, None)
    assert log.poll(70, None, T0 + timedelta(hours=3)) is None
    assert log.trips == [] and log.open_trip is None


def test_charging_and_short_rides_count_for_segment():
    log = L.TripLog()
    odo(log, 100.0, T0)
    for soc in (90, 88, 95, 100, 97):   # 2 % ohne erkannte Abwesenheit, Laden, 3 %
        log.poll(soc, None, None)
    assert log.segment["used_pct"] == 5
    seg = odo(log, 102.0, T0)
    assert seg["prozent_pro_km"] == 2.5 and seg["temperatur"] is None


def test_km_without_consumption_and_reset():
    log = L.TripLog()
    odo(log, 100.0, T0)
    assert odo(log, 110.0, T0) is None and log.segment["odo_start"] == 110.0
    assert odo(log, 5.0, T0) is None and log.segment["odo_start"] == 5.0
    assert log.segments == []


def test_roundtrip_and_limits():
    log = L.TripLog()
    log.poll(100, None, None)
    for i in range(40):
        t = T0 + timedelta(hours=i)
        log.leave(t, 100, None)
        log.prev_soc = 100
        log.poll(95, None, t + timedelta(minutes=30))
    assert len(log.trips) == L.MAX_TRIPS
    assert log.trips[0]["start"] == (T0 + timedelta(hours=39)).isoformat()
    clone = L.TripLog(log.as_dict())
    assert clone.as_dict() == log.as_dict()

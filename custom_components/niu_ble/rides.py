"""Fahrten aus der NIU-Cloud aufbereiten und auswerten. Reine Logik ohne Home Assistant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

# Unterhalb dieser Strecke ist ein Verbrauch pro km nicht aussagekräftig (Rundung auf ganze %).
MIN_KM_FOR_RATE = 0.3


def _corrected(ms: int, tz: tzinfo) -> datetime:
    """Cloud-Zeitstempel korrigieren.

    Die Cloud liefert die Zeitstempel um den UTC-Versatz der Zeitzone verschoben
    (für Mitteleuropa also zu früh). Der Versatz wird daher aufaddiert; so macht es
    auch die Referenz-App aus dem Projekt niu-kqi.
    """
    raw = datetime.fromtimestamp(ms / 1000, UTC)
    return raw + (raw.astimezone(tz).utcoffset() or timedelta(0))


def normalize(raw: dict[str, Any], tz: tzinfo, capacity_wh: float | None) -> dict[str, Any] | None:
    """Einen Cloud-Datensatz in einen Fahrtenbuch-Eintrag wandeln.

    Liefert None für Datensätze, die keine Fahrt sind (Ladevorgänge, kaputte Einträge).
    """
    try:
        track_id = str(raw["trackId"])
        start = _corrected(int(raw["startTime"]), tz)
        end = _corrected(int(raw["endTime"]), tz)
        km = round(int(raw["distance"]) / 1000, 2)
        seconds = int(raw.get("ridingtime") or 0)
    except (KeyError, TypeError, ValueError):
        return None

    used = raw.get("power_consumption")
    used = int(used) if isinstance(used, (int, float)) else None
    if used is not None and used < 0:
        # Akku ist gestiegen: ein als Fahrt protokollierter Ladevorgang.
        return None
    implied_kmh = km / (seconds / 3600) if seconds > 0 else 0
    if not used and seconds > 1200 and implied_kmh < 5:
        return None

    rate = round(used / km, 2) if used is not None and km >= MIN_KM_FOR_RATE else None
    speed = raw.get("avespeed")
    return {
        "id": track_id,
        "start": start.isoformat(),
        "ende": end.isoformat(),
        "km": km,
        "dauer_min": round(seconds / 60, 1),
        "tempo": round(float(speed), 1) if isinstance(speed, (int, float)) else None,
        "verbrauch": used,
        "prozent_pro_km": rate,
        "wh_pro_km": round(rate / 100 * capacity_wh, 1) if rate is not None and capacity_wh else None,
        "temperatur": None,
    }


def sorted_rides(rides: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Fahrten, neueste zuerst."""
    return sorted(rides.values(), key=lambda r: r["start"], reverse=True)


def average_rate(
    rides: list[dict[str, Any]], now: datetime, days: int | None = None
) -> float | None:
    """Streckengewichteter Verbrauch in %/km (Summe % / Summe km), optional der letzten n Tage."""
    limit = (now - timedelta(days=days)).isoformat() if days else None
    used = km = 0.0
    for ride in rides:
        if ride.get("verbrauch") is None or (limit and ride["start"] < limit):
            continue
        used += ride["verbrauch"]
        km += ride["km"]
    return round(used / km, 2) if km >= MIN_KM_FOR_RATE else None


def total_km(rides: list[dict[str, Any]]) -> float:
    """Summe aller Fahrten in km."""
    return round(sum(r["km"] for r in rides), 1)

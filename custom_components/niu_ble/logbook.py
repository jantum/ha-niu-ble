"""Fahrtenbuch und Verbrauch pro km. Reine Logik ohne Home-Assistant-Abhängigkeit.

Begriffe:
- Fahrt: eine Abwesenheit aus der Bluetooth-Reichweite, bei der der Akku gesunken ist.
- Abschnitt: Zeitraum zwischen zwei Ständen des (Cloud-)Kilometerzählers. Der in dieser
  Zeit verbrauchte Akku geteilt durch die gefahrenen km ergibt den Verbrauch pro km.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

MAX_TRIPS = 30
MAX_SEGMENTS = 30
MIN_SEGMENT_KM = 0.5


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


class TripLog:
    """Zustand des Fahrtenbuchs; lässt sich als dict speichern und laden."""

    def __init__(self, stored: dict[str, Any] | None = None) -> None:
        stored = stored or {}
        self.trips: list[dict[str, Any]] = stored.get("trips", [])
        self.segments: list[dict[str, Any]] = stored.get("segments", [])
        self.open_trip: dict[str, Any] | None = stored.get("open_trip")
        self.prev_soc: int | None = stored.get("prev_soc")
        self.segment: dict[str, Any] = stored.get("segment") or self._new_segment(None, None)

    @staticmethod
    def _new_segment(odometer: float | None, now: datetime | None) -> dict[str, Any]:
        return {
            "odo_start": odometer,
            "used_pct": 0,
            "temps": [],
            "start": now.isoformat() if now else None,
        }

    def as_dict(self) -> dict[str, Any]:
        """Zustand zum Speichern."""
        return {
            "trips": self.trips,
            "segments": self.segments,
            "open_trip": self.open_trip,
            "prev_soc": self.prev_soc,
            "segment": self.segment,
        }

    # ------------------------------------------------------------------ Fahrten

    def leave(self, when: datetime, soc: int | None, temp: float | None) -> None:
        """Roller hat die Reichweite verlassen."""
        if self.open_trip is None:
            self.open_trip = {"start": when.isoformat(), "soc_start": soc, "temp_start": temp}

    def poll(
        self,
        soc: int | None,
        temp: float | None,
        returned_at: datetime | None,
    ) -> dict[str, Any] | None:
        """Erfolgreicher Abruf. Schließt eine offene Fahrt und zählt Verbrauch.

        Liefert die abgeschlossene Fahrt, falls eine eingetragen wurde.
        """
        if soc is None:
            return None

        # Jeder Rückgang des Akkustands zählt als Verbrauch des laufenden Abschnitts.
        if self.prev_soc is not None and soc < self.prev_soc:
            self.segment["used_pct"] += self.prev_soc - soc
        self.prev_soc = soc

        trip = self.open_trip
        if trip is None or returned_at is None:
            return None
        self.open_trip = None

        soc_start = trip.get("soc_start")
        if soc_start is None or soc_start - soc < 1:
            # Kein Verbrauch: Funkloch oder schlafender Roller, keine Fahrt.
            return None

        start = datetime.fromisoformat(trip["start"])
        temps = [t for t in (trip.get("temp_start"), temp) if t is not None]
        temp_avg = _avg(temps)
        if temp_avg is not None:
            self.segment["temps"].append(temp_avg)
        entry = {
            "start": trip["start"],
            "ende": returned_at.isoformat(),
            "dauer_min": max(0, round((returned_at - start).total_seconds() / 60)),
            "soc_start": soc_start,
            "soc_ende": soc,
            "verbrauch": soc_start - soc,
            "temperatur": temp_avg,
        }
        self.trips.insert(0, entry)
        del self.trips[MAX_TRIPS:]
        return entry

    # ---------------------------------------------------------------- Abschnitte

    @property
    def waiting_for_sync(self) -> bool:
        """Es wurde Akku verbraucht, aber die km dazu fehlen noch."""
        return self.segment["used_pct"] > 0

    def odometer(
        self,
        value: float | None,
        now: datetime,
        *,
        capacity_wh: float | None,
        rides_unsynced: bool | None,
        in_range: bool,
    ) -> dict[str, Any] | None:
        """Kilometerstand auswerten. Liefert einen abgeschlossenen Abschnitt oder None."""
        if value is None:
            return None
        seg = self.segment
        if seg["odo_start"] is None:
            seg["odo_start"] = value
            seg["start"] = seg["start"] or now.isoformat()
            return None

        km = round(value - seg["odo_start"], 2)
        if km < 0:
            # Zähler zurückgesetzt oder anderes Fahrzeug: neu aufsetzen.
            self.segment = self._new_segment(value, now)
            return None
        if km < MIN_SEGMENT_KM:
            return None
        # Erst abrechnen, wenn der Roller zurück ist und alle Fahrten in der Cloud sind.
        if not in_range or self.open_trip is not None or rides_unsynced:
            return None

        used = seg["used_pct"]
        if used <= 0:
            # km ohne erfassten Verbrauch (z. B. vor der Installation): nur Startpunkt verschieben.
            self.segment = self._new_segment(value, now)
            return None

        entry = {
            "start": seg["start"],
            "ende": now.isoformat(),
            "km": km,
            "verbrauch": used,
            "prozent_pro_km": round(used / km, 2),
            "wh_pro_km": round(used / 100 * capacity_wh / km, 1) if capacity_wh else None,
            "temperatur": _avg(seg["temps"]),
        }
        self.segments.insert(0, entry)
        del self.segments[MAX_SEGMENTS:]
        self.segment = self._new_segment(value, now)
        return entry

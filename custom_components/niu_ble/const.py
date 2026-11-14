"""Konstanten der NIU-BLE-Integration."""

from __future__ import annotations

DOMAIN = "niu_ble"

CONF_BLE_PASSWORD = "ble_password"
CONF_BLE_AES = "ble_aes"
CONF_TRACKER = "phone_tracker"
CONF_INTERVAL = "scan_interval"
CONF_NIU_ACCOUNT = "niu_account"
CONF_NIU_PASSWORD = "niu_password"
CONF_NIU_PW_HASH = "niu_password_hash"
CONF_TEMPERATURE = "temperature_entity"

DEFAULT_INTERVAL_MIN = 5
MIN_INTERVAL_MIN = 1
MAX_INTERVAL_MIN = 60

# Lesegruppen (je max. 5 Felder pro Request).
GROUP_STATE = ["bms_soc_rt", "bms_bat_sta_rt", "bms_bat_to_cap", "bms_c_cont", "db_k_f_code"]
GROUP_RIDE = ["db_k_estimated_mileage", "foc_k_gears", "foc_k_max_speed", "foc_k_realtime_status1"]
GROUP_FIRMWARE = ["db_k_sw_ver", "foc_k_s_ver", "bms_s_ver_n", "ecu_bt_ver"]

# bms_bat_sta_rt, am KQi 300P beobachtet (13.11.2026):
#   0 = Ladegerät ab, 32768 = Ladegerät angesteckt, 32770 = angesteckt und lädt
BIT_CHARGER_CONNECTED = 0x8000
BIT_CHARGING = 0x0002

# foc_k_realtime_status1
BIT_RIDES_UNSYNCED = 0x0800

# Nennspannung des Akkus (V); dient nur zur Umrechnung mAh -> Wh (Näherung).
RATED_VOLTAGE = 48

RESPONSE_TIMEOUT = 4.0
POLL_TIMEOUT = 45.0

# foc_k_gears, am KQi 300P beobachtet (14.11.2026): Power-Taste wechselt zwischen 1 und 2,
# beim Einschalten zeigt das Display „E-Save“ bei 1. Weitere Modi (Custom, Fußgänger) unbestätigt.
RIDE_MODES: dict[int, str] = {1: "e_save", 2: "sport"}

# Fehlercodes laut Störungstabelle im NIU-KQi-Handbuch.
FAULTS: dict[int, str] = {
    0: "Kein Fehler",
    1: "Controller: Leistungsstufe defekt",
    2: "Bremse: Fehler am Bremshebel",
    3: "Gasgriff: Fehler",
    4: "Controller: Überstrom",
    5: "Controller: Übertemperatur",
    6: "Controller: Treiberspannung fehlerhaft",
    7: "Controller: Kommunikationsfehler",
    8: "Controller: Unter- oder Überspannung",
    10: "Motor: blockiert",
    11: "Motor: Phasenausfall",
    12: "Motor: Hall-Sensor defekt",
    13: "Motor: Übertemperatur",
    31: "Akku: MOSFET defekt",
    32: "Akku: Feuchtigkeit",
    33: "Akku: Unterbrechung oder Zellen-Ungleichgewicht",
    34: "Akku: zu kalt zum Laden",
    35: "Akku: Übertemperatur",
    37: "Akku: Kurzschlussschutz",
    38: "Akku: Ladeüberstrom",
    39: "Akku: Entladeüberstrom",
    40: "Akku: Überladewarnung",
    41: "Akku: tiefentladen",
    42: "Akku: Kommunikationsfehler",
}

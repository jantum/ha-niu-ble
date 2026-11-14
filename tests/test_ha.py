"""Integrationstest in echter HA-Laufzeit mit gemocktem Roller."""
from unittest.mock import patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "niu_ble"
ADDR = "AA:BB:CC:DD:EE:FF"
VALUES = {
    "bms_soc_rt": 63, "bms_bat_sta_rt": 32770, "bms_bat_to_cap": 9822, "bms_c_cont": 35,
    "db_k_f_code": 0, "db_k_estimated_mileage": 0, "foc_k_gears": 2, "foc_k_max_speed": 200,
    "foc_k_realtime_status1": 2177, "db_k_sw_ver": "KBC38V05", "foc_k_s_ver": "KBE38D05",
    "bms_s_ver_n": "K3D35V16", "ecu_bt_ver": "KBQ44V01",
}


async def test_config_flow_validation_and_create(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "nope", "ble_password": "short", "ble_aes": "abc"}
    )
    assert bad["errors"] == {
        CONF_ADDRESS: "invalid_mac", "ble_password": "invalid_password", "ble_aes": "invalid_aes"
    }
    with patch("custom_components.niu_ble.async_setup_entry", return_value=True):
        ok = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ADDRESS: ADDR.lower(), "ble_password": "A" * 16, "ble_aes": "B" * 16,
             "phone_tracker": "device_tracker.phone"},
        )
    assert ok["type"] == "create_entry", ok
    assert ok["data"] == {CONF_ADDRESS: ADDR, "ble_password": "A" * 16, "ble_aes": "B" * 16}
    assert ok["options"] == {"scan_interval": 5, "phone_tracker": "device_tracker.phone"}


async def test_config_flow_auth_test(hass: HomeAssistant) -> None:
    from custom_components.niu_ble.protocol import NiuAuthError

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with patch("custom_components.niu_ble.config_flow.bluetooth.async_ble_device_from_address", return_value=object()), \
         patch("custom_components.niu_ble.config_flow.async_read_scooter", side_effect=NiuAuthError("x")):
        res = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDR, "ble_password": "A" * 16}
        )
    assert res["errors"] == {"base": "invalid_auth"}


async def test_setup_states_tracker_and_restore(hass: HomeAssistant) -> None:
    hass.config.latitude, hass.config.longitude = 52.5, 13.4
    hass.states.async_set(
        "device_tracker.phone", "not_home",
        {"latitude": 48.1, "longitude": 11.5, "gps_accuracy": 12, "source_type": "gps"},
    )
    hass.states.async_set("sensor.outdoor", "4.0")
    entry = MockConfigEntry(
        domain=DOMAIN, title="NIU KQi", unique_id=ADDR,
        data={CONF_ADDRESS: ADDR, "ble_password": "A" * 16, "ble_aes": "B" * 16},
        options={"scan_interval": 5, "phone_tracker": "device_tracker.phone",
                 "temperature_entity": "sensor.outdoor"},
    )
    entry.add_to_hass(hass)

    calls = []

    async def fake_read(device, password, aes, groups):
        calls.append(groups)
        return {k: VALUES[k] for g in groups for k in g}

    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=object()), \
         patch("custom_components.niu_ble.coordinator.bluetooth.async_address_present", return_value=True), \
         patch("custom_components.niu_ble.coordinator.async_read_scooter", side_effect=fake_read):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        coordinator = entry.runtime_data
        assert len(calls) == 1 and len(calls[0]) == 3  # inkl. Firmware beim ersten Abruf
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(calls[1]) == 2  # Firmware nur einmal

    reg = er.async_get(hass)
    ids = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert len(ids) == 26, ids
    names = {e.unique_id.split("_", 1)[1]: hass.states.get(e.entity_id).name for e in er.async_entries_for_config_entry(reg, entry.entry_id) if hass.states.get(e.entity_id)}
    assert names["consumption_7d"] != "NIU KQi" and names["logbook"] != "NIU KQi", names

    def state(key):
        return hass.states.get(ids[f"{ADDR}_{key}"])

    assert state("battery").state == "63"
    assert state("estimated_range").state == "unknown"  # 0 = Platzhalter
    assert state("ride_mode").state == "sport" and state("ride_mode").attributes["rohwert"] == 2
    assert float(state("max_speed").state) == 20.0
    assert state("fault_code").state == "0"
    assert state("fault_code").attributes["description"] == "Kein Fehler"
    assert state("battery_capacity").state == "9822"
    assert state("in_range").state == "on"
    assert state("charger_connected").state == "on"
    assert state("charging").state == "on"
    assert state("rides_unsynced").state == "on"
    assert state("last_update").state not in ("unknown", "unavailable")
    assert state("location").state == "home"
    assert state("firmware_display") is None  # standardmäßig deaktiviert

    # Roller fährt weg: Akku bleibt stehen, Ladezustand unbekannt, Standort folgt dem Handy
    coordinator._async_on_unavailable(None)
    await hass.async_block_till_done()
    assert state("in_range").state == "off"
    assert state("battery").state == "63"
    assert state("charging").state == "unknown"
    loc = state("location")
    assert loc.state == "not_home" and loc.attributes["latitude"] == 48.1
    hass.states.async_set(
        "device_tracker.phone", "not_home",
        {"latitude": 48.2, "longitude": 11.6, "gps_accuracy": 8, "source_type": "gps"},
    )
    await hass.async_block_till_done()
    assert state("location").attributes["latitude"] == 48.2

    # Abruf unterwegs: kein Gerät -> Werte bleiben, kein Fehler
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        await coordinator.async_refresh()
    assert coordinator.last_update_success and state("battery").state == "63"

    # Rückkehr: Advertisement -> sofort zuhause + Abruf
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=object()), \
         patch("custom_components.niu_ble.coordinator.async_read_scooter",
               return_value={"bms_soc_rt": 40, "bms_bat_sta_rt": 0}):
        coordinator._async_on_advertisement(None, None)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert state("location").state == "home"
    assert state("battery").state == "40"
    assert state("charger_connected").state == "off"

    # ohne NIU-Konto bleibt das Fahrtenbuch leer
    assert state("logbook").state == "unknown"

    # Lesefehler: alte Werte bleiben
    from bleak.exc import BleakError
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=object()), \
         patch("custom_components.niu_ble.coordinator.async_read_scooter", side_effect=BleakError("boom")):
        await coordinator.async_refresh()
    assert state("battery").state == "40"

    # Neustart: Werte kommen aus dem Speicher, auch wenn der Roller weg ist
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert state("battery").state == "40"
    assert state("in_range").state == "off"

    # Optionen ändern
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == "form"
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        res = await hass.config_entries.options.async_configure(res["flow_id"], {"scan_interval": 10})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options == {"scan_interval": 10}
    assert entry.runtime_data.update_interval.total_seconds() == 600

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cloud_rides(hass: HomeAssistant, aioclient_mock) -> None:
    from datetime import datetime, timedelta, timezone

    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.states.async_set("sensor.outdoor", "4.5")
    now = datetime.now(timezone.utc)
    recent = int((now - timedelta(hours=1)).timestamp() * 1000)
    old = int(datetime(2026, 11, 5, 9, 7, tzinfo=timezone.utc).timestamp() * 1000)

    def ride(track, start, meters, used):
        return {"trackId": track, "startTime": start, "endTime": start + 720000, "distance": meters,
                "ridingtime": 710, "avespeed": 19.9, "power_consumption": used, "date": 0}

    aioclient_mock.post("https://account-fk.niu.com/v3/api/oauth2/token",
                        json={"status": 0, "data": {"token": {"access_token": "T", "token_expires_in": 86400}}})
    aioclient_mock.get("https://app-api-fk.niu.com/v5/scooter/list",
                       json={"status": 0, "data": {"items": [{"sn_id": "OTHER"}, {"sn_id": "SN1"}]}})
    aioclient_mock.get("https://app-api-fk.niu.com/v5/ble/bleinfo?sn=OTHER",
                       json={"status": 0, "data": {"bleMac": "11:22:33:44:55:66"}})
    aioclient_mock.get("https://app-api-fk.niu.com/v5/ble/bleinfo?sn=SN1",
                       json={"status": 0, "data": {"bleMac": "AABBCCDDEEFF"}})
    aioclient_mock.post("https://app-api-fk.niu.com/v5/track/list/v3",
                        json={"status": 0, "data": {"items": [
                            ride("r2", recent, 3600, 22), ride("r1", old, 1500, 9), ride("chg", old, 0, -30)]}})

    entry = MockConfigEntry(
        domain=DOMAIN, title="NIU KQi", unique_id=ADDR,
        data={CONF_ADDRESS: ADDR, "ble_password": "A" * 16, "ble_aes": "B" * 16,
              "niu_account": "me@example.com", "niu_password_hash": "0" * 32},
        options={"scan_interval": 5, "temperature_entity": "sensor.outdoor"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    reg = er.async_get(hass)
    ids = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}

    def state(key):
        return hass.states.get(ids[f"{ADDR}_{key}"])

    coordinator = entry.runtime_data
    assert coordinator.cloud_error is None and coordinator._serial == "SN1"
    assert state("logbook").state == "2"
    rides = state("logbook").attributes["fahrten"]
    assert [r["id"] for r in rides] == ["r2", "r1"]
    assert rides[0]["km"] == 3.6 and rides[0]["verbrauch"] == 22 and rides[0]["prozent_pro_km"] == 6.11
    assert rides[0]["temperatur"] == 4.5 and rides[1]["temperatur"] is None   # alt, keine Statistik
    assert rides[0]["wh_pro_km"] is None                                       # Kapazität noch nie gelesen
    assert state("last_trip_consumption").state == "22"
    assert float(state("consumption_pct_per_km").state) == 6.11
    assert float(state("consumption_7d").state) == 6.11
    assert float(state("consumption_total").state) == round(31 / 5.1, 2)
    assert float(state("ridden_distance").state) == 5.1
    assert state("cloud_sync").state not in ("unknown", "unavailable")

    # Diagnose: Roh-Datensatz mit Feldnamen, Orte und Zugangsdaten unkenntlich
    from custom_components.niu_ble.diagnostics import async_get_config_entry_diagnostics
    coordinator._cloud.last_raw = {**coordinator._cloud.last_raw, "startPoint": {"lat": 52.5}, "riding_mode": 2}
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "riding_mode" in diag["cloud_raw_ride_keys"] and diag["cloud_raw_ride"]["riding_mode"] == 2
    assert diag["cloud_raw_ride"]["startPoint"] == "**REDACTED**"
    assert diag["entry"]["data"]["niu_password_hash"] == "**REDACTED**"
    assert diag["entry"]["data"]["ble_password"] == "**REDACTED**"

    # zweiter Abruf: nichts Neues, nichts doppelt
    assert await coordinator.async_sync_rides() == 0
    assert state("logbook").state == "2"

    # Neustart: Fahrten kommen aus dem Speicher, auch wenn die Cloud ausfällt
    import aiohttp
    aioclient_mock.clear_requests()
    aioclient_mock.post("https://account-fk.niu.com/v3/api/oauth2/token", exc=aiohttp.ClientError("offline"))
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert state("logbook").state == "2"
    assert entry.runtime_data.cloud_error is not None

    # Optionen: falsches Passwort wird abgelehnt, richtiges gespeichert (nur als Hash)
    aioclient_mock.clear_requests()
    aioclient_mock.post("https://account-fk.niu.com/v3/api/oauth2/token",
                        json={"status": 1131, "desc": "Incorrect Login Details"})
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"scan_interval": 5, "niu_account": "new@example.com", "niu_password": "falsch"})
    assert res["type"] == "form" and res["errors"] == {"base": "cloud_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.post("https://account-fk.niu.com/v3/api/oauth2/token",
                        json={"status": 0, "data": {"token": {"access_token": "T2", "token_expires_in": 86400}}})
    aioclient_mock.post("https://app-api-fk.niu.com/v5/track/list/v3", json={"status": 0, "data": {"items": []}})
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        res = await hass.config_entries.options.async_configure(
            res["flow_id"], {"scan_interval": 5, "niu_account": "new@example.com", "niu_password": "geheim"})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert res["type"] == "create_entry"
    assert entry.data["niu_account"] == "new@example.com"
    assert entry.data["niu_password_hash"] == "e8636ea013e682faf61f56ce1cb1ab5c"
    assert "geheim" not in str(entry.data) and "niu_password" not in entry.options
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_old_rides_are_reimported(hass: HomeAssistant, hass_storage) -> None:
    """Fahrten aus 0.3.0 (verschobene Zeiten) werden verworfen und neu geholt."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="NIU KQi", unique_id=ADDR,
        data={CONF_ADDRESS: ADDR, "ble_password": "A" * 16, "ble_aes": "B" * 16},
        options={"scan_interval": 5},
    )
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"values": {"bms_soc_rt": 50}, "rides": {"x": {"id": "x", "start": "2026-11-05T11:07:32+00:00",
                 "km": 1.0, "verbrauch": 5}}},
    }
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    assert coordinator.ride_list == [] and coordinator.data["bms_soc_rt"] == 50
    assert await hass.config_entries.async_unload(entry.entry_id)

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
    hass.states.async_set("sensor.odo", "384.33")
    hass.states.async_set("sensor.outdoor", "4.0")
    entry = MockConfigEntry(
        domain=DOMAIN, title="NIU KQi", unique_id=ADDR,
        data={CONF_ADDRESS: ADDR, "ble_password": "A" * 16, "ble_aes": "B" * 16},
        options={"scan_interval": 5, "phone_tracker": "device_tracker.phone",
                 "odometer_entity": "sensor.odo", "temperature_entity": "sensor.outdoor"},
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
    assert len(ids) == 21, ids

    def state(key):
        return hass.states.get(ids[f"{ADDR}_{key}"])

    assert state("battery").state == "63"
    assert state("estimated_range").state == "unknown"  # 0 = Platzhalter
    assert state("ride_mode").state == "2"
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

    # Fahrtenbuch: 63 -> 40 % während der Abwesenheit
    assert state("logbook").state == "1"
    trip = state("logbook").attributes["fahrten"][0]
    assert (trip["soc_start"], trip["soc_ende"], trip["verbrauch"], trip["temperatur"]) == (63, 40, 23, 4.0)
    assert state("logbook").attributes["unterwegs"] is False
    assert state("last_trip_consumption").state == "23"
    assert state("consumption_pct_per_km").state == "unknown"
    assert state("consumption_pct_per_km").attributes["wartet_auf_sync"] is True

    # Cloud-km steigen, Roller meldet aber noch unsynchronisierte Fahrten -> warten
    hass.states.async_set("sensor.odo", "390.0")
    await hass.async_block_till_done()
    assert state("consumption_pct_per_km").state == "unknown"
    # nach dem App-Sync: Bit gelöscht, Zähler final -> Abschnitt 23 % / 10 km
    hass.states.async_set("sensor.odo", "394.33")
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=object()), \
         patch("custom_components.niu_ble.coordinator.async_read_scooter",
               return_value={"bms_soc_rt": 40, "foc_k_realtime_status1": 129}):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert float(state("consumption_pct_per_km").state) == 2.3
    assert float(state("consumption_wh_per_km").state) == round(0.23 * 9822 * 48 / 1000 / 10, 1)
    attrs = state("consumption_pct_per_km").attributes
    assert attrs["km"] == 10.0 and attrs["temperatur"] == 4.0 and attrs["wartet_auf_sync"] is False

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
    assert state("logbook").state == "1" and float(state("consumption_pct_per_km").state) == 2.3

    # Optionen ändern
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == "form"
    with patch("custom_components.niu_ble.coordinator.bluetooth.async_ble_device_from_address", return_value=None):
        res = await hass.config_entries.options.async_configure(res["flow_id"], {"scan_interval": 10})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options == {"scan_interval": 10}
    assert entry.runtime_data.update_interval.total_seconds() == 600

    assert await hass.config_entries.async_unload(entry.entry_id)

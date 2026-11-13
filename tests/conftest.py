import custom_components.niu_ble  # noqa: F401  (vor dem Test-Harness laden)
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def mock_bt(enable_bluetooth):
    yield

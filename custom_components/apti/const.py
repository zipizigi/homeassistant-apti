"""Constants for the APTi integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "apti"
NAME = "APTi"
MANUFACTURER = "APTi"
DEFAULT_SCAN_INTERVAL_HOURS = 24
DEFAULT_SCAN_INTERVAL = timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS)
API_BASE_URL = "https://api-main.apti.co.kr"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
PAYMENT_STATE_CODES: tuple[str, ...] = ("001", "002", "003", "004", "005")
CONF_MBL_TOKEN = "mbl_token"
CONF_BACKFILL_DONE = "backfill_done"


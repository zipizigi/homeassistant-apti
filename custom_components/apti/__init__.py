"""The APTi integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import APTiApiError, APTiAuthError, APTiClient
from .const import CONF_MBL_TOKEN, DEFAULT_SCAN_INTERVAL_HOURS, DOMAIN, PLATFORMS
from .coordinator import APTiDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up APTi from a config entry."""
    session = async_get_clientsession(hass)

    async def _on_token_update(token: str) -> None:
        """Persist a newly issued mbl_token into the config entry."""
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_MBL_TOKEN: token},
        )

    client = APTiClient(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        mbl_token=entry.data.get(CONF_MBL_TOKEN) or None,
        on_token_update=_on_token_update,
    )

    interval_hours = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_HOURS),
    )
    coordinator = APTiDataUpdateCoordinator(
        hass,
        entry,
        client,
        update_interval=timedelta(hours=int(interval_hours)),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except APTiAuthError as err:
        raise ConfigEntryAuthFailed("APTi authentication failed") from err
    except APTiApiError as err:
        raise ConfigEntryNotReady(f"APTi API unavailable: {err}") from err
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to initialize APTi integration: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload APTi config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)

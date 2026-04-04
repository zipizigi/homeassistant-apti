"""Button platform for APTi — manual energy history backfill."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import APTiClient
from .backfill import async_backfill_energy_statistics
from .const import DOMAIN, MANUFACTURER
from .entity import DEVICE_ENERGY


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up APTi button entities."""
    client: APTiClient = hass.data[DOMAIN][entry.entry_id]["client"]
    async_add_entities([AptiBackfillButton(entry, client)])


class AptiBackfillButton(ButtonEntity):
    """Button to manually trigger energy history backfill."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:database-import"
    _attr_translation_key = "backfill"

    def __init__(self, entry: ConfigEntry, client: APTiClient) -> None:
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_{DEVICE_ENERGY}_backfill"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_{DEVICE_ENERGY}")},
        )

    async def async_press(self) -> None:
        """Trigger energy history backfill on demand."""
        await async_backfill_energy_statistics(self.hass, self._client)

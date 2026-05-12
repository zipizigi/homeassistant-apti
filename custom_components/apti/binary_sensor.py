"""Binary sensors for APTi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import APTiDataUpdateCoordinator
from .entity import (
    AptiCoordinatorEntity,
    DEVICE_ACCOUNT,
    DEVICE_MANAGEMENT_FEE,
)


def _yn_to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"Y", "YES", "TRUE", "1"}:
        return True
    if text in {"N", "NO", "FALSE", "0"}:
        return False
    return None


@dataclass
class AptiBinarySensorDescription(BinarySensorEntityDescription):
    """Definition for binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda _: None
    device_key: str = DEVICE_ACCOUNT


DESCRIPTIONS: tuple[AptiBinarySensorDescription, ...] = (
    AptiBinarySensorDescription(
        key="mgmt_payment_completed",
        name="관리비 납부완료",
        icon="mdi:check-decagram",
        device_key=DEVICE_MANAGEMENT_FEE,
        value_fn=lambda d: bool(d.get("management_fee", {}).get("paymentCompleted")),
    ),
    AptiBinarySensorDescription(
        key="mgmt_auto_transfer",
        name="관리비 자동이체",
        icon="mdi:bank-check",
        device_key=DEVICE_MANAGEMENT_FEE,
        value_fn=lambda d: _yn_to_bool(d.get("manage_home", {}).get("autoTransferYN")),
    ),
    AptiBinarySensorDescription(
        key="electronic_bill",
        name="전자고지",
        icon="mdi:email-fast",
        device_key=DEVICE_ACCOUNT,
        value_fn=lambda d: _yn_to_bool(d.get("account", {}).get("electronicBill")),
    ),
)


class AptiBinarySensor(AptiCoordinatorEntity, BinarySensorEntity):
    """Simple binary sensor wrapper."""

    def __init__(
        self,
        coordinator: APTiDataUpdateCoordinator,
        config_entry: ConfigEntry,
        description: AptiBinarySensorDescription,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            f"binary_{description.key}",
            device_key=description.device_key,
        )
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up APTi binary sensors."""
    coordinator: APTiDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    async_add_entities(
        AptiBinarySensor(coordinator, config_entry, description) for description in DESCRIPTIONS
    )

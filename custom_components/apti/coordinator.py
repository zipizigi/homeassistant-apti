"""Data coordinator for the APTi integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import APTiApiError, APTiAuthError, APTiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class APTiDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and merge APTi API payloads."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: APTiClient,
        update_interval,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh all data required by entities."""
        try:
            await self._client.async_ensure_token()
        except APTiAuthError as err:
            raise ConfigEntryAuthFailed("APTi authentication failed") from err
        except APTiApiError as err:
            raise UpdateFailed(f"APTi token validation failed: {err}") from err

        based_month = dt_util.now().strftime("%Y%m")

        tasks: dict[str, asyncio.Future] = {
            "account_v3_detail": asyncio.create_task(
                self._client.async_get_user_information_detail_v3()
            ),
            "user_information2": asyncio.create_task(
                self._client.async_get_user_information2()
            ),
            "apt_information": asyncio.create_task(
                self._client.async_get_apt_information()
            ),
            "management_fee_main": asyncio.create_task(
                self._client.async_get_management_fee_main()
            ),
            "management_fee_analysis": asyncio.create_task(
                self._client.async_get_management_fee_analysis()
            ),
            "management_fee": asyncio.create_task(
                self._client.async_get_management_fee_history()
            ),
            "management_fee_energy": asyncio.create_task(
                self._client.async_get_management_fee_energy()
            ),
            "management_fee_energy_use": asyncio.create_task(
                self._client.async_get_management_fee_energy_use()
            ),
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        raw: dict[str, Any] = {}
        errors: dict[str, str] = {}

        for key, result in zip(tasks, results, strict=False):
            if isinstance(result, APTiAuthError):
                raise ConfigEntryAuthFailed("APTi token rejected") from result
            if isinstance(result, Exception):
                errors[key] = str(result)
                continue
            raw[key] = result

        manage_home = self._adapt_v3_to_home(
            raw.get("management_fee_main"), raw.get("management_fee_analysis")
        )
        manage_energy = self._adapt_v3_energy_to_v2(
            raw.get("management_fee_energy"),
            raw.get("management_fee_energy_use"),
        )
        manage_auto_discount = self._adapt_v3_to_auto_discount(
            raw.get("user_information2")
        )
        management_fee = raw.get("management_fee")
        if not manage_home and not isinstance(management_fee, dict):
            raise UpdateFailed("APTi core management endpoints returned no data")

        account = self._build_account(
            raw.get("account_v3_detail"), raw.get("apt_information")
        )

        data: dict[str, Any] = {
            "account": account,
            "manage_home": manage_home,
            "management_fee": management_fee if isinstance(management_fee, dict) else {},
            "manage_auto_discount": manage_auto_discount,
            "manage_energy": manage_energy,
            "based_month": based_month,
        }

        if errors:
            _LOGGER.debug("APTi partial refresh errors: %s", errors)
            data["partial_errors"] = errors

        return data

    def _build_account(
        self,
        account_v3_detail: dict[str, Any] | None,
        apt_information: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge user detail (v3) with apt metadata (v3) for the account view."""
        merged: dict[str, Any] = {}
        if isinstance(account_v3_detail, dict):
            merged.update(account_v3_detail)
        if isinstance(apt_information, dict):
            merged.update(apt_information)
        return merged

    def _adapt_v3_to_home(
        self,
        main: dict[str, Any] | None,
        analysis: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Project v3 management-fee/{main,analysis} onto the legacy manage_home
        shape consumed by existing sensors.

        - /main provides paymentInfo (month/due/auto) + managementFeeInfo (billYm).
        - /analysis adds energyCondition (area, myFee, avgFee, compAvg).
        Fields with no v3 source (bfMonthFee) stay absent."""
        info_main = main if isinstance(main, dict) else {}
        info_analysis = analysis if isinstance(analysis, dict) else {}

        mfi = info_main.get("managementFeeInfo") or {}
        payment = info_main.get("paymentInfo") or {}
        energy_condition = info_analysis.get("energyCondition") or {}

        payment_information: list[dict[str, Any]] | None = None
        method = payment.get("autoPaymentMethod")
        if method:
            payment_information = [{"method": method}]

        if not info_main and not info_analysis:
            return {}

        return {
            "monthFee": payment.get("monthFee") or info_analysis.get("monthFee"),
            "bfDueFee": payment.get("bfDueFee"),
            "billYm": (
                mfi.get("lastBillYm")
                or mfi.get("startBillYm")
                or info_analysis.get("lastBillYm")
            ),
            "autoTransferYN": payment.get("autoPaymentYN"),
            "paymentInformation": payment_information,
            "area": energy_condition.get("area"),
            "energyCondition": {
                "myFee": energy_condition.get("myFee"),
                "avgFee": energy_condition.get("avgFee"),
                "compAvg": energy_condition.get("compAvg"),
            },
        }

    def _adapt_v3_to_auto_discount(
        self, user_information2: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Project v3 /user/information2 onto the legacy manage_auto_discount shape.

        v2 honeyYn (꿀단지 자동할인) is approximated by v3 autoDiscountStatus.
        v2 schBillYm has no v3 counterpart in observed traffic — left absent.
        """
        if not isinstance(user_information2, dict):
            return {}
        return {
            "honeyYn": user_information2.get("autoDiscountStatus"),
        }

    def _adapt_v3_energy_to_v2(
        self,
        energy_payload: dict[str, Any] | None,
        energy_use_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Project v3 /management-fee/{energy,energy-use} onto the legacy
        manage_energy shape, merging fee/use with meter readings.

        Each category dict ends up with: fee, use, unit, currentNeedle,
        previousNeedle, currentUse, previousUse, lastYearUse (whichever the
        upstream provided).
        """
        energy = energy_payload if isinstance(energy_payload, dict) else {}
        use = energy_use_payload if isinstance(energy_use_payload, dict) else {}
        if not energy and not use:
            return {}

        category_map = (
            ("electric", "electricInfo"),
            ("water", "waterInfo"),
            ("heat", "heatInfo"),
            ("hotwater", "hotWaterInfo"),
        )

        out: dict[str, dict[str, Any]] = {}
        for cat, key in category_map:
            base = energy.get(key) if isinstance(energy.get(key), dict) else {}
            meter = use.get(key) if isinstance(use.get(key), dict) else {}
            merged: dict[str, Any] = {}
            merged.update(base)
            merged.update({k: v for k, v in meter.items() if k not in merged})
            out[cat] = merged

        return {"energy": out}

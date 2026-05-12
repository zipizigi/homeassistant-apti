"""Config flow for APTi integration (token-only mode).

ID/password login is intentionally not exposed in the UI. The integration
operates purely on a manually supplied mbl-token. The legacy
:func:`_validate_login` helper is preserved so the credential-based flow can
be re-enabled in the future, but no UI step routes to it.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import APTiApiError, APTiAuthError, APTiClient
from .const import CONF_MBL_TOKEN, DEFAULT_SCAN_INTERVAL_HOURS, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _validate_token(
    hass: HomeAssistant, mbl_token: str
) -> tuple[dict[str, Any], str]:
    """Validate a mobile token by fetching the user profile. Returns (info, mbl_token)."""
    client = APTiClient(
        async_get_clientsession(hass),
        account_id="",
        password="",
        mbl_token=mbl_token,
    )

    info_v3_detail = await client.async_get_user_information_detail_v3()
    if not isinstance(info_v3_detail, dict):
        raise APTiApiError("APTi profile lookup returned no data")

    return info_v3_detail, mbl_token


async def _validate_login(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Validate account credentials against APTi API. Returns (data, info, mbl_token).

    NOTE: Not wired to any UI step. Kept for future re-enablement of the
    credential-based flow. After the v2→v3 migration, only v3 detail is used
    for profile lookup.
    """
    client = APTiClient(
        async_get_clientsession(hass),
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
    )
    login_payload = await client.async_login(force=True)

    info_v3_detail = await client.async_get_user_information_detail_v3()
    info: dict[str, Any] | None = (
        info_v3_detail if isinstance(info_v3_detail, dict) else None
    )

    if info is None:
        info = {"userId": login_payload.get("userId") or data[CONF_USERNAME]}

    mbl_token = client.mbl_token or ""
    return data, info, mbl_token


def _build_entry_title(info: dict[str, Any], fallback: str) -> str:
    """Create a readable config entry title."""
    apt_name = str(info.get("aptName") or "").strip()
    dong = str(info.get("dong") or info.get("aptDong") or "").strip()
    ho = str(info.get("ho") or info.get("aptHo") or "").strip()
    if apt_name and dong and ho:
        return f"{apt_name} {dong}-{ho}"
    if apt_name:
        return apt_name
    return fallback


class APTiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for APTi (token-only)."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return options flow."""
        return APTiOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Initial setup: accept a mbl-token only."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = (user_input.get(CONF_MBL_TOKEN) or "").strip()
            try:
                info, mbl_token = await _validate_token(self.hass, token)
            except APTiAuthError:
                errors["base"] = "invalid_auth"
            except APTiApiError as err:
                _LOGGER.warning("APTi token validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during APTi token validation")
                errors["base"] = "unknown"
            else:
                unique_id = str(info.get("userId") or mbl_token)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=_build_entry_title(info, unique_id),
                    data={
                        CONF_USERNAME: str(info.get("userId") or ""),
                        CONF_PASSWORD: "",
                        CONF_MBL_TOKEN: mbl_token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_MBL_TOKEN): str,
            }),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Triggered by HA when an APTiAuthError surfaces during refresh."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Prompt user to paste a fresh mbl-token."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry

        if user_input is not None and entry is not None:
            token = (user_input.get(CONF_MBL_TOKEN) or "").strip()
            try:
                info, mbl_token = await _validate_token(self.hass, token)
            except APTiAuthError:
                errors["base"] = "invalid_auth"
            except APTiApiError as err:
                _LOGGER.warning("APTi token re-auth validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during APTi token re-auth")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_MBL_TOKEN: mbl_token},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_MBL_TOKEN): str,
            }),
            errors=errors,
        )


class APTiOptionsFlow(OptionsFlow):
    """Options flow for APTi."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage options."""
        if user_input is not None:
            new_token = user_input.pop(CONF_MBL_TOKEN, "").strip()
            if new_token:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={**self._config_entry.data, CONF_MBL_TOKEN: new_token},
                )
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_SCAN_INTERVAL,
                        DEFAULT_SCAN_INTERVAL_HOURS,
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
                vol.Optional(
                    CONF_MBL_TOKEN,
                    default=self._config_entry.data.get(CONF_MBL_TOKEN, ""),
                ): str,
            }),
        )

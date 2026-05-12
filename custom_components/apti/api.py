"""Async API client for APTi."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Callable, Coroutine

from aiohttp import ClientError, ClientResponseError, ClientSession
from yarl import URL

from .const import API_BASE_URL

DEFAULT_TIMEOUT_SECONDS = 20

# Random pre-request delay to scatter burst traffic and avoid looking like a
# tight-loop bot. Applied to every authenticated request.
_REQUEST_DELAY_MIN_SECONDS = 0.05
_REQUEST_DELAY_MAX_SECONDS = 1.0

# 실측 기준 앱 버전 및 디바이스 정보 (앱 v3.3.49, iPhone 16, iOS 18.7)
_APP_VERSION = "3.3.49"
_IOS_VERSION = "18_7"

# Flutter 네이티브 Dart HTTP 클라이언트 헤더
# 사용: login, check-token, user/information, sync/*, apt/*, sdi/home/* 등
_HEADERS_DART: dict[str, str] = {
    "User-Agent": "Dart/3.11 (dart:io)",
    "app-version": _APP_VERSION,
    "adid": "",
    "adid-idfa": "",
    "accept-encoding": "gzip",
}

# WKWebView 헤더 (azweb.apti.co.kr 웹앱에서 호출)
# 사용: management-fee/*, user/information/detail
_HEADERS_WEBVIEW: dict[str, str] = {
    "User-Agent": (
        f"Mozilla/5.0 (iPhone; CPU iPhone OS {_IOS_VERSION} like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "app-version": _APP_VERSION,
    "adid": "00000000-0000-0000-0000-000000000000",
    "origin": "https://azweb.apti.co.kr",
    "referer": "https://azweb.apti.co.kr/",
    "accept": "application/json, text/plain, */*",
    "accept-language": "ko-KR,ko;q=0.9",
    "sec-fetch-site": "same-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "priority": "u=3, i",
    "accept-encoding": "gzip, deflate, br, zstd",
}

# WebView 패턴을 사용하는 경로 prefix
_WEBVIEW_PATHS = ("/v3/api/management-fee/", "/v3/api/user/information/detail")


def _is_webview_path(path: str) -> bool:
    return any(path.startswith(p) for p in _WEBVIEW_PATHS)


class APTiApiError(Exception):
    """Raised when the APTi API returns an error."""


class APTiAuthError(APTiApiError):
    """Raised when APTi authentication fails."""


class APTiClient:
    """Thin async client for the APTi mobile APIs."""

    def __init__(
        self,
        session: ClientSession,
        account_id: str,
        password: str,
        mbl_token: str | None = None,
        on_token_update: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._session = session
        self._account_id = account_id
        self._password = password
        self._mbl_token: str | None = mbl_token
        self._on_token_update = on_token_update

    @property
    def account_id(self) -> str:
        """Return configured account id."""
        return self._account_id

    @property
    def mbl_token(self) -> str | None:
        """Return current mobile token."""
        return self._mbl_token

    async def async_ensure_token(self) -> None:
        """Verify that a mobile token is configured. Token validity is checked
        lazily on the first real request — any 401/expired response raises
        APTiAuthError, which surfaces as a re-auth request to the user."""
        if not self._mbl_token:
            raise APTiAuthError(
                "APTi mobile token is not configured; provide a fresh token"
            )

    async def async_login(self, *, force: bool = False) -> dict[str, Any]:
        """Authenticate using phone login and cache mbl-token.

        NOTE: Auto-invocation has been disabled — the integration now operates
        in token-only mode. This method is kept so it can still be called
        directly (e.g. from a manual debugging script or a future re-enable),
        but the request pipeline no longer triggers it on auth failures."""
        if self._mbl_token and not force:
            return {"mblToken": self._mbl_token}

        headers = {
            **_HEADERS_DART,
            "content-type": "application/json",
            "push-token": "",
            "finger-push-token": "",
        }
        try:
            async with self._session.post(
                str(URL(API_BASE_URL).with_path("/api/v2/login/phone")),
                headers=headers,
                json={"id": self._account_id, "password": self._password},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            ) as response:
                payload = await self._decode_json(response)
                if response.status >= 400:
                    message = self._extract_error_message(payload)
                    raise APTiAuthError(f"{message} (HTTP {response.status})")
        except APTiAuthError:
            raise
        except (ClientError, ClientResponseError, TimeoutError) as err:
            raise APTiApiError(str(err)) from err

        token = payload.get("mblToken") or payload.get("mbl_token")
        if not token:
            raise APTiAuthError(
                payload.get("message") or "APTi login failed (missing token)"
            )

        self._mbl_token = token
        if self._on_token_update:
            await self._on_token_update(token)
        return payload

    async def async_get_user_information_detail_v3(self) -> dict[str, Any] | None:
        """Fetch user detail profile (v3). Returns None when endpoint is unavailable."""
        try:
            return await self._request("GET", "/v3/api/user/information/detail")
        except APTiApiError:
            return None

    async def async_get_user_information2(self) -> dict[str, Any] | None:
        """Fetch user auto-discount / auto-pay flags (v3).

        Replaces parts of v2 /api/v2/manage/auto-discount: exposes
        autoDiscountStatus, monthlyRentAutoStatus, naverPayDiscountStatus.
        """
        try:
            return await self._request("GET", "/v3/api/user/information2")
        except APTiApiError:
            return None

    async def async_get_apt_information(self) -> dict[str, Any] | None:
        """Fetch apartment metadata (v3). Provides aptName, address, menuCode."""
        try:
            return await self._request("GET", "/v3/api/apt/information")
        except APTiApiError:
            return None

    async def async_get_management_fee_main(self) -> dict[str, Any] | None:
        """Fetch management-fee main summary (v3 replacement for v2 manage/home)."""
        try:
            return await self._request("GET", "/v3/api/management-fee/main")
        except APTiApiError:
            return None

    async def async_get_management_fee_energy(self) -> dict[str, Any] | None:
        """Fetch energy summary (v3 replacement for v2 manage/energy)."""
        try:
            return await self._request("GET", "/v3/api/management-fee/energy")
        except APTiApiError:
            return None

    async def async_get_management_fee_energy_use(self) -> dict[str, Any] | None:
        """Fetch meter readings + previous/year-ago usage per category (v3).

        Provides currentNeedle, previousNeedle, currentUse, previousUse,
        lastYearUse, unit per electric/water/heat/hotwater category.
        """
        try:
            return await self._request("GET", "/v3/api/management-fee/energy-use")
        except APTiApiError:
            return None

    async def async_get_management_fee_analysis(self) -> dict[str, Any] | None:
        """Fetch management-fee analysis (v3).

        Provides energyCondition (area, myFee, avgFee, compAvg) — used to
        restore the v2 manage_home.area and energyCondition.* sensors.
        """
        try:
            return await self._request("GET", "/v3/api/management-fee/analysis")
        except APTiApiError:
            return None

    async def async_get_energy_analysis(self) -> dict[str, Any] | None:
        """Fetch 13-month energy analysis (historical monthly data)."""
        try:
            return await self._request("GET", "/v3/api/management-fee/energy-analysis")
        except APTiApiError:
            return None

    async def async_get_management_fee_history(
        self, bill_ym: str | None = None
    ) -> dict[str, Any]:
        """Fetch management fee detail."""
        path = "/v3/api/management-fee/history"
        if bill_ym:
            path = f"{path}/{bill_ym}"
        return await self._request("GET", path)


    def _build_headers(self, path: str) -> dict[str, str]:
        """Return Dart or WebView headers based on API path."""
        base = _HEADERS_WEBVIEW if _is_webview_path(path) else _HEADERS_DART
        headers = dict(base)
        if self._mbl_token:
            headers["mbl-token"] = self._mbl_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth_required: bool = True,
    ) -> dict[str, Any] | list[Any]:
        """Execute an API request. Auth failures raise APTiAuthError so the
        integration can prompt for a fresh mbl-token (no automatic re-login)."""
        if auth_required and not self._mbl_token:
            raise APTiAuthError("APTi mobile token is not configured")

        await asyncio.sleep(
            random.uniform(_REQUEST_DELAY_MIN_SECONDS, _REQUEST_DELAY_MAX_SECONDS)
        )

        url = str(URL(API_BASE_URL).with_path(path))
        headers = self._build_headers(path)

        try:
            async with self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            ) as response:
                payload = await self._decode_json(response)

                if response.status >= 400:
                    message = self._extract_error_message(payload)
                    detail = f"{message} (HTTP {response.status} {path})"
                    if auth_required and self._is_auth_failure(response.status, payload):
                        raise APTiAuthError(detail)
                    if response.status in (401, 403):
                        raise APTiAuthError(detail)
                    raise APTiApiError(detail)

                if auth_required and self._is_auth_failure(response.status, payload):
                    message = self._extract_error_message(payload)
                    raise APTiAuthError(f"{message} ({path})")

                return payload
        except APTiAuthError:
            raise
        except (ClientError, ClientResponseError, TimeoutError) as err:
            raise APTiApiError(str(err)) from err

    async def _decode_json(self, response) -> dict[str, Any] | list[Any]:
        """Decode JSON payload; if body is empty return an empty dict."""
        text = await response.text()
        if not text:
            return {}
        try:
            parsed = await response.json(content_type=None)
        except ValueError as err:
            raise APTiApiError(f"Non-JSON response: {text[:160]}") from err
        if isinstance(parsed, (dict, list)):
            return parsed
        raise APTiApiError("Unexpected API response type")

    def _is_auth_failure(self, http_status: int, payload: dict[str, Any] | list[Any]) -> bool:
        """Detect auth-expired conditions."""
        if http_status in (401, 403):
            return True
        if isinstance(payload, dict):
            status = str(payload.get("status", ""))
            code = str(payload.get("code", ""))
            message = str(payload.get("message", ""))
            if status in {"90001", "90002", "90005"}:
                return True
            if code in {"90001", "90002", "90005"}:
                return True
            if "로그인" in message and ("만료" in message or "필요" in message):
                return True
        return False

    def _extract_error_message(self, payload: dict[str, Any] | list[Any]) -> str:
        """Extract best-effort error message from payload."""
        if isinstance(payload, dict):
            for key in ("message", "description", "status", "code"):
                value = payload.get(key)
                if value not in (None, ""):
                    return str(value)
        return "APTi API request failed"

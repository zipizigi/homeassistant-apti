"""App version and User-Agent cache for APTi integration."""

from __future__ import annotations

import time

from aiohttp import ClientSession

from .const import DEFAULT_APP_VERSION, DEFAULT_IOS_VERSION

ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup?id=1457413104&country=kr"
IPSW_DEVICE_URL = "https://api.ipsw.me/v4/device/iPhone17,3"
CACHE_TTL_SECONDS = 86400  # 24 hours

_UA_TEMPLATE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS {ios_version} like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)


class AppVersionCache:
    """Caches app version and iOS UA, refreshing once per day."""

    def __init__(self) -> None:
        self._app_version: str = DEFAULT_APP_VERSION
        self._ios_version: str = DEFAULT_IOS_VERSION
        self._last_fetch: float = 0.0

    @property
    def app_version(self) -> str:
        return self._app_version

    @property
    def user_agent(self) -> str:
        return _UA_TEMPLATE.format(ios_version=self._ios_version)

    def is_stale(self) -> bool:
        return time.monotonic() - self._last_fetch > CACHE_TTL_SECONDS

    async def async_refresh(self, session: ClientSession) -> None:
        """Fetch latest versions if cache is stale. No-op otherwise."""
        if not self.is_stale():
            return
        await self._fetch_app_version(session)
        await self._fetch_ios_version(session)
        self._last_fetch = time.monotonic()

    async def _fetch_app_version(self, session: ClientSession) -> None:
        try:
            async with session.get(ITUNES_LOOKUP_URL, timeout=10) as resp:
                data = await resp.json(content_type=None)
                version = (data.get("results") or [{}])[0].get("version", "")
                if version:
                    self._app_version = version
        except Exception:
            pass  # keep previous value

    async def _fetch_ios_version(self, session: ClientSession) -> None:
        try:
            async with session.get(IPSW_DEVICE_URL, timeout=10) as resp:
                data = await resp.json(content_type=None)
                for fw in data.get("firmwares", []):
                    version: str = fw.get("version", "")
                    if version and not fw.get("beta", False):
                        # "18.5" → "18_5"
                        self._ios_version = version.replace(".", "_")
                        break
        except Exception:
            pass  # keep previous value

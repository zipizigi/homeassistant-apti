"""Historical energy statistics backfill from APTi energy-analysis API."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData, StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import APTiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# (statistic_id_suffix, chart_key, value_key, unit, name)
# 외부 통계 ID: apti:{statistic_id_suffix} — 센서 엔티티와 별개로 Recorder에 저장됨
_ENERGY_STATS: list[tuple[str, str, str, str, str]] = [
    ("energy_electric_use",  "electric",  "use", UnitOfEnergy.KILO_WATT_HOUR,  "전기 사용량"),
    ("energy_electric_fee",  "electric",  "fee", "KRW",                         "전기 요금"),
    ("energy_hotwater_use",  "hotWater",  "use", UnitOfVolume.CUBIC_METERS,     "급탕 사용량"),
    ("energy_hotwater_fee",  "hotWater",  "fee", "KRW",                         "급탕 요금"),
    ("energy_water_use",     "water",     "use", UnitOfVolume.CUBIC_METERS,     "수도 사용량"),
    ("energy_water_fee",     "water",     "fee", "KRW",                         "수도 요금"),
    ("energy_heat_use",      "heat",      "use", UnitOfVolume.CUBIC_METERS,     "난방 사용량"),
    ("energy_heat_fee",      "heat",      "fee", "KRW",                         "난방 요금"),
]


def _month_start(yyyymm: str) -> datetime:
    """Convert 'yyyyMM' to timezone-aware datetime at the first of that month."""
    tz = dt_util.get_default_time_zone()
    return datetime(int(yyyymm[:4]), int(yyyymm[4:6]), 1, 0, 0, tzinfo=tz)


async def async_backfill_energy_statistics(
    hass: HomeAssistant, client: APTiClient
) -> bool:
    """Fetch energy-analysis and insert historical statistics as external stats.

    월별 사용량(리셋되는 값)이므로 센서와 분리된 외부 통계(apti:*)로 관리.
    누적합은 코드에서 직접 계산해 Energy Dashboard에서 사용 가능하도록 함.
    Returns True when at least one series was inserted successfully.
    """
    data = await client.async_get_energy_analysis()

    if not isinstance(data, dict):
        _LOGGER.warning("APTi energy-analysis returned no data — skipping backfill")
        return False

    chart_info: dict[str, Any] = data.get("energyChartInfo", {})
    inserted = 0

    for stat_suffix, chart_key, value_key, unit, name in _ENERGY_STATS:
        chart: list[dict[str, Any]] = (
            chart_info.get(chart_key, {})
            .get("myHouse", {})
            .get("chart", [])
        )
        if not chart:
            _LOGGER.debug("APTi backfill: no chart data for %s", chart_key)
            continue

        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=name,
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:{stat_suffix}",
            unit_of_measurement=unit,
        )

        stats: list[StatisticData] = []
        cumulative = 0.0
        for row in sorted(chart, key=lambda x: x["month"]):
            val = float(row.get(value_key) or 0)
            cumulative += val
            stats.append(
                StatisticData(
                    start=_month_start(row["month"]),
                    state=val,
                    sum=cumulative,
                )
            )

        async_add_external_statistics(hass, metadata, stats)
        _LOGGER.debug(
            "APTi backfilled %d months for %s:%s",
            len(stats), DOMAIN, stat_suffix,
        )
        inserted += 1

    _LOGGER.info("APTi energy backfill complete (%d series inserted)", inserted)
    return inserted > 0

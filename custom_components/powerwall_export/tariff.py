"""Tariff format conversion and date-limited export prices; no HA dependency."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import math
from typing import Any
from zoneinfo import ZoneInfo


class ExportError(Exception):
    """A failure which must be surfaced rather than assumed successful."""


def number(value: Any, label: str, low: float = 0, high: float = 100) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportError(f"{label} is missing or not numeric.")
    if not math.isfinite(value) or not low <= value <= high:
        raise ExportError(f"{label} must be between {low} and {high}.")
    return float(value)


def extract_tariff(data: Any) -> dict:
    """Accept documented write content and the observed tariff_rate read wrappers."""
    if not isinstance(data, dict):
        raise ExportError("No readable tariff was returned; no settings changed.")
    if isinstance(data.get("seasons"), dict) and isinstance(data.get("energy_charges"), dict):
        return deepcopy(data)
    for key in ("response", "tou_settings", "tariff_content_v2", "tariff_content", "tariff_rate"):
        if isinstance(data.get(key), dict):
            try:
                return extract_tariff(data[key])
            except ExportError:
                pass
    raise ExportError("Current tariff content is unavailable; refusing to overwrite it.")


def normalize_tariff(raw: dict, currency: str = "GBP") -> dict:
    """Convert flattened GET rates/periods to Tesla's write-side shape.

    Preserve returned content and add only write-schema defaults. Do not invent
    a missing buy/sell schedule or rates. Read-back comparison is mandatory.
    """
    result = extract_tariff(raw)
    if not isinstance(result.get("sell_tariff"), dict):
        raise ExportError("A complete buy AND sell tariff is required for restoration.")
    for side in (result, result["sell_tariff"]):
        if not side.get("seasons") or not side.get("energy_charges"):
            raise ExportError("The tariff is missing a season or energy prices.")
        side.setdefault("currency", currency)
        if not side["currency"]:
            side["currency"] = currency
        if side["currency"] not in ("GBP", "EUR", "USD"):
            raise ExportError("Unsupported tariff currency.")
        for key in ("monthly_minimum_bill", "monthly_charges", "min_applicable_demand", "max_applicable_demand"):
            side.setdefault(key, 0)
        side.setdefault("daily_charges", [])
        side.setdefault("daily_demand_charges", {})
        side.setdefault("demand_charges", {"ALL": {"rates": {"ALL": 0}}})
        for key in ("energy_charges", "demand_charges"):
            if not isinstance(side[key], dict):
                raise ExportError(f"Unsupported {key} structure.")
            for label, prices in list(side[key].items()):
                if not isinstance(prices, dict):
                    raise ExportError("Unsupported tariff rate structure.")
                if "rates" not in prices:
                    side[key][label] = {"rates": deepcopy(prices)}
                rates = side[key][label]["rates"]
                if not isinstance(rates, dict):
                    raise ExportError("Unsupported tariff rates.")
                for value in rates.values():
                    number(value, "Tariff price", 0, 1000000)
        for season in side["seasons"].values():
            if not isinstance(season, dict) or not isinstance(season.get("tou_periods"), dict):
                raise ExportError("Unsupported tariff season structure.")
            for label, periods in list(season["tou_periods"].items()):
                if isinstance(periods, list):
                    season["tou_periods"][label] = {"periods": deepcopy(periods)}
                if not isinstance(season["tou_periods"][label], dict) or not isinstance(season["tou_periods"][label].get("periods"), list):
                    raise ExportError("Unsupported time-of-use period structure.")
    result.setdefault("version", 1)
    return result


def write_tariff(raw: dict) -> dict:
    """Build a fresh Tesla v2 write payload from a normalized read tariff.

    Inactive placeholder seasons (often zero-filled Winter) are only excluded
    when an active season fully covers the year. Never invent or silently
    change price periods; reject unsafe / unfamiliar schedules.
    """
    source = normalize_tariff(raw)
    allowed = ("version", "utility", "code", "name", "currency",
               "monthly_minimum_bill", "monthly_charges", "min_applicable_demand",
               "max_applicable_demand", "daily_charges", "daily_demand_charges",
               "demand_charges", "energy_charges", "seasons")
    def side_write(side: dict) -> dict:
        seasons = side["seasons"]
        active = {}
        for key, season in seasons.items():
            dates = (season.get("fromMonth"), season.get("fromDay"),
                     season.get("toMonth"), season.get("toDay"))
            if all(isinstance(v, int) and v > 0 for v in dates):
                active[key] = deepcopy(season)
            elif (dates == (0, 0, 0, 0)
                  and not season.get("tou_periods")
                  and not side["energy_charges"].get(key, {}).get("rates")):
                continue
            else:
                raise ExportError("Unsupported inactive or incomplete tariff season. No write.")
        if not active:
            raise ExportError("No active tariff seasons. No write.")
        # Check every calendar date has exactly one valid season.
        from datetime import date, timedelta
        date0 = date(2000, 1, 1)
        for i in range(366):
            day = date0 + timedelta(days=i)
            md = (day.month, day.day)
            found = 0
            for season in active.values():
                lo = (season["fromMonth"], season["fromDay"])
                hi = (season["toMonth"], season["toDay"])
                if lo <= md <= hi if lo <= hi else (md >= lo or md <= hi):
                    found += 1
            if found != 1:
                raise ExportError("Tariff seasons overlap or do not cover the year. No write.")
        output = {key: deepcopy(side[key]) for key in allowed if key in side}
        output["seasons"] = active
        for field in ("energy_charges", "demand_charges"):
            output[field] = {k: deepcopy(v) for k, v in side[field].items()
                             if k == "ALL" or k in active}
        return output
    output = side_write(source)
    output["sell_tariff"] = side_write(source["sell_tariff"])
    return output


def _sorted(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sorted(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return sorted((_sorted(v) for v in value), key=repr)
    return value


def tariff_matches(actual: dict, expected: dict) -> bool:
    """Ignore display metadata, not rates, schedules or billing amounts."""
    try:
        actual = normalize_tariff(actual, expected.get("currency") or "GBP")
        expected = normalize_tariff(expected, expected.get("currency") or "GBP")
    except ExportError:
        return False
    keys = ("currency", "seasons", "energy_charges", "demand_charges", "daily_charges", "daily_demand_charges", "monthly_charges", "monthly_minimum_bill", "min_applicable_demand", "max_applicable_demand")
    # Tesla removes zero-date, empty placeholder seasons from its write/read
    # representation. Compare active, validated write content instead.
    try:
        actual = write_tariff(actual)
        expected = write_tariff(expected)
    except ExportError:
        return False
    for left, right in ((actual, expected), (actual["sell_tariff"], expected["sell_tariff"])):
        if _sorted({k: left[k] for k in keys}) != _sorted({k: right[k] for k in keys}):
            return False
    return True


def _period(start: int, end: int) -> dict:
    return {"fromDayOfWeek": 0, "toDayOfWeek": 6,
            "fromHour": start // 60, "fromMinute": start % 60,
            "toHour": (end // 60) % 24, "toMinute": end % 60}


def build_export_tariff(start: datetime, end: datetime, time_zone: str, currency: str = "GBP") -> dict:
    """Use annual date seasons so a missed restore cannot repeat tomorrow.

    An annual tariff cannot express a year: a year-long outage could repeat
    the window next year. This is a backstop, NOT an offline restore mechanism.
    DST-transition windows are rejected rather than guessing wall-clock times.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ExportError("Start and end must have a time zone.")
    if not 0 < (end - start).total_seconds() <= 6 * 3600:
        raise ExportError("An export window must be positive and at most six hours.")
    try:
        zone = ZoneInfo(time_zone)
    except (ValueError, KeyError) as exc:
        raise ExportError("Invalid Powerwall time zone.") from exc
    start, end = start.astimezone(zone), end.astimezone(zone)
    if start.utcoffset() != end.utcoffset():
        raise ExportError("Windows crossing a daylight-saving clock change are not supported.")
    # Round the end DOWN: never add time beyond the requested session.
    start, end = start.replace(second=0, microsecond=0), end.replace(second=0, microsecond=0)
    if end <= start:
        raise ExportError("The minute-rounded export window is empty.")
    event_days: dict[tuple[int, int], tuple[int, int]] = {}
    day = start.date()
    while day <= end.date():
        a = start.hour * 60 + start.minute if day == start.date() else 0
        b = end.hour * 60 + end.minute if day == end.date() else 1440
        if b > a:
            event_days[(day.month, day.day)] = (a, b)
        day += timedelta(days=1)
    # Leap year template includes February 29 while covering every annual date.
    dates = [date(2000, 1, 1) + timedelta(days=i) for i in range(366)]
    segments: list[tuple[date, date, tuple[int, int] | None]] = []
    for day in dates:
        window = event_days.get((day.month, day.day))
        if segments and window is None and segments[-1][2] is None:
            segments[-1] = (segments[-1][0], day, None)
        else:
            segments.append((day, day, window))
    seasons, buy, sell, demand = {}, {"ALL": {"rates": {"ALL": 0}}}, {"ALL": {"rates": {"ALL": 0}}}, {"ALL": {"rates": {"ALL": 0}}}
    for i, (first, last, window) in enumerate(segments):
        label = f"Season{i + 1}"
        periods = {}
        if window is None:
            periods["OFF_PEAK"] = {"periods": [_period(0, 1440)]}
        else:
            a, b = window
            periods["ON_PEAK"] = {"periods": [_period(a, b)]}
            off = ([_period(0, a)] if a else []) + ([_period(b, 1440)] if b < 1440 else [])
            if off:
                periods["OFF_PEAK"] = {"periods": off}
        seasons[label] = {"fromMonth": first.month, "fromDay": first.day,
                          "toMonth": last.month, "toDay": last.day, "tou_periods": periods}
        # Synthetic optimisation signals, NOT the customer's Octopus prices.
        buy[label] = {"rates": {p: (10.0 if p == "ON_PEAK" else 0.10) for p in periods}}
        sell[label] = {"rates": {p: (9.0 if p == "ON_PEAK" else 0.0) for p in periods}}
        demand[label] = {"rates": {}}
    base = {"version": 1, "utility": "Home Assistant temporary control",
            "code": "HA-PWE-TEMPORARY", "name": f"HA temporary export until {end.isoformat()}",
            "currency": currency, "monthly_minimum_bill": 0, "monthly_charges": 0,
            "min_applicable_demand": 0, "max_applicable_demand": 0,
            "daily_charges": [], "daily_demand_charges": {},
            "demand_charges": demand, "seasons": seasons}
    result = {**deepcopy(base), "energy_charges": buy}
    result["sell_tariff"] = {**deepcopy(base), "energy_charges": sell}
    return normalize_tariff(result, currency)

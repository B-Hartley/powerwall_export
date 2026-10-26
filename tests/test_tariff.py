from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pwe_under_test.tariff import ExportError, build_export_tariff, extract_tariff, normalize_tariff, tariff_matches
from conftest import base_tariff


def rate_at(tariff, moment, selling=False):
    side = tariff['sell_tariff'] if selling else tariff
    md = (moment.month, moment.day)
    minute = moment.hour * 60 + moment.minute
    matches = []
    for label, s in side['seasons'].items():
        if (s['fromMonth'], s['fromDay']) <= md <= (s['toMonth'], s['toDay']):
            for period_name, block in s['tou_periods'].items():
                for p in block['periods']:
                    a = p['fromHour'] * 60 + p['fromMinute']
                    b = p['toHour'] * 60 + p['toMinute'] or 1440
                    if a <= minute < b:
                        matches.append(side['energy_charges'][label]['rates'][period_name])
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize('start', [
    datetime(2026,9,11,17,15,tzinfo=timezone.utc),
    datetime(2026,9,11,22,45,tzinfo=timezone.utc),
    datetime(2026,12,31,23,40,tzinfo=timezone.utc),
    datetime(2028,2,29,17,0,tzinfo=timezone.utc),
    datetime(2026,1,1,0,0,tzinfo=timezone.utc),
])
def test_window_prices_and_minute_coverage(start):
    end = start + timedelta(minutes=60)
    t = build_export_tariff(start, end, 'Europe/London')
    zone = ZoneInfo('Europe/London')
    # Exhaustively test the two adjacent days at minute resolution.
    for delta in range(-120, 180):
        moment = (start + timedelta(minutes=delta)).astimezone(zone)
        sell = rate_at(t, moment, True)
        buy = rate_at(t, moment)
        assert sell == (9 if 0 <= delta < 60 else 0)
        assert buy >= sell >= 0
    # No repeated export tomorrow / next week.
    assert rate_at(t, (start + timedelta(days=7)).astimezone(zone), True) == 0


def test_no_season_gaps_or_overlap_leap_year():
    start = datetime(2026,9,11,17,0,tzinfo=timezone.utc)
    t = build_export_tariff(start, start + timedelta(hours=1), 'Europe/London')
    for i in range(366):
        d = date(2000,1,1) + timedelta(days=i)
        assert sum((s['fromMonth'],s['fromDay']) <= (d.month,d.day) <= (s['toMonth'],s['toDay']) for s in t['seasons'].values()) == 1


def test_end_at_midnight_has_no_next_day_spike():
    start = datetime(2026,9,11,22,30,tzinfo=timezone.utc)
    t = build_export_tariff(start, start + timedelta(minutes=30), 'Europe/London')
    assert rate_at(t, datetime(2026,9,12,0,0), True) == 0


@pytest.mark.parametrize('start', [datetime(2026,3,29,0,30,tzinfo=timezone.utc), datetime(2026,10,25,0,30,tzinfo=timezone.utc)])
def test_clock_changes_refused(start):
    with pytest.raises(ExportError, match='daylight-saving'):
        build_export_tariff(start, start + timedelta(hours=2), 'Europe/London')


def test_flattened_read_round_trips():
    original = base_tariff()
    flat = deepcopy(original)
    for side in (flat,flat['sell_tariff']):
        for kind in ('energy_charges','demand_charges'):
            side[kind] = {label: value['rates'] for label,value in side[kind].items()}
        for s in side['seasons'].values():
            s['tou_periods'] = {label: block['periods'] for label,block in s['tou_periods'].items()}
    assert tariff_matches(flat, original)
    assert normalize_tariff(flat) == original


def test_metadata_does_not_mask_price_changes():
    expected = base_tariff()
    actual = deepcopy(expected)
    actual['name'] = 'A name rewritten by the API'
    assert tariff_matches(actual, expected)
    actual['energy_charges']['All year']['rates']['ON_PEAK'] = 0.99
    assert not tariff_matches(actual, expected)


def test_missing_sell_rejected():
    t = base_tariff()
    del t['sell_tariff']
    with pytest.raises(ExportError, match='buy AND sell'):
        normalize_tariff(t)


def test_extract_nested_wrappers():
    original = base_tariff()
    assert extract_tariff({'response':{'tou_settings':{'tariff_content_v2':original}}}) == original


@pytest.mark.parametrize('bad', [-1,float('nan'),float('inf'),True,'0.30'])
def test_invalid_prices_rejected(bad):
    t = base_tariff()
    t['energy_charges']['All year']['rates']['ON_PEAK'] = bad
    with pytest.raises(ExportError):
        normalize_tariff(t)


def test_synthetic_prices_not_applied_to_saved_original():
    original = base_tariff()
    before = deepcopy(original)
    start = datetime(2026,9,11,17,0,tzinfo=timezone.utc)
    build_export_tariff(start, start + timedelta(hours=1), 'Europe/London', original['currency'])
    assert original == before

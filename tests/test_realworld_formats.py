"""Regression tests for user-observed rolling and manual Tesla tariffs."""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
from pwe_under_test.advance import build_advance_tariff, _spans
from pwe_under_test.tariff import normalize_tariff


def period(a, b):
    return {'fromDayOfWeek':0,'toDayOfWeek':6,'fromHour':a//60,'fromMinute':a%60,'toHour':b//60,'toMinute':b%60}


def basic_side(rolling=False, sell=False):
    if rolling:
        keys=['Yesterday','Today','Tomorrow','TwoDays','ThreeDays']
        boundaries=[(24,24),(25,25),(26,26),(27,27),(28,23)]
    else:
        keys=['Summer','Winter']
        boundaries=[(1,31),(0,0)]
    seasons={}; charges={'ALL': {'ALL':0}}; demand={'ALL': {'ALL':0}}
    for key,(first,last) in zip(keys,boundaries):
        if key=='Winter':
            seasons[key]={'fromDay':0,'toDay':0,'fromMonth':0,'toMonth':0,'tou_periods':{}}
            charges[key]={};demand[key]={}
            continue
        if rolling:
            tou={'hour_0_minute_0':[period(0,330)], 'hour_23_minute_30':[period(1410,0)], 'hour_5_minute_30':[period(330,1410)]} if not sell else {'hour_0_minute_0':[period(0,0)]}
            rates={'hour_0_minute_0':0.066,'hour_23_minute_30':0.066,'hour_5_minute_30':0.3588} if not sell else {'hour_0_minute_0':0.12}
        else:
            tou={'OFF_PEAK':[period(1410,330)],'ON_PEAK':[period(330,1410)]}
            rates={'OFF_PEAK':0.12,'ON_PEAK':0.12} if sell else {'OFF_PEAK':0.07,'ON_PEAK':0.29}
        seasons[key]={'fromDay':first,'toDay':last,'fromMonth':10 if rolling else 1,'toMonth':10 if rolling else 12,'tou_periods':tou}
        charges[key]=rates
        demand[key]={}
    return {'name':'Tesla rolling' if rolling else 'Bottle Cap','utility':'Octopus','energy_charges':charges,'demand_charges':demand,'seasons':seasons}


def make_tariff(rolling):
    tariff=basic_side(rolling)
    tariff['sell_tariff']=basic_side(rolling,True)
    return tariff


def test_overnight_time_of_use_shifts_to_following_day():
    spans=_spans(period(1410,330))
    assert len(spans)==14
    assert (0,1410,1440) in spans
    assert (1,0,330) in spans


def test_user_manual_bottlecap_tariff_preview():
    original=make_tariff(False)
    before=deepcopy(original)
    a=datetime(2026,10,26,18,tzinfo=timezone.utc)
    result=build_advance_tariff(original,a,a+timedelta(hours=1),'Europe/London')
    assert original==before
    for side in (result,result['sell_tariff']):
        event=side['seasons']['Summer_HA_EVENT']
        assert (event['fromDay'],event['toDay'])==(26,26)
        assert side['energy_charges']['Summer_HA_EVENT']['rates']['HA_SESSION']==(2.0 if side is result['sell_tariff'] else 2.1)


def test_user_rolling_five_day_tariff_preview():
    original=make_tariff(True)
    a=datetime(2026,10,26,18,tzinfo=timezone.utc)
    result=build_advance_tariff(original,a,a+timedelta(hours=1),'Europe/London')
    for side in (result,result['sell_tariff']):
        assert 'Tomorrow_HA_EVENT' in side['seasons']
        assert 'Yesterday' in side['seasons']
        assert 'ThreeDays' in side['seasons']
        assert side['energy_charges']['Tomorrow_HA_EVENT']['rates']['HA_SESSION']==(2.0 if side is result['sell_tariff'] else 2.1)


def test_rolling_wraparound_season_preview():
    original=make_tariff(True)
    a=datetime(2026,11,2,18,tzinfo=timezone.utc)
    result=build_advance_tariff(original,a,a+timedelta(hours=1),'Europe/London')
    for side in (result,result['sell_tariff']):
        assert 'ThreeDays_HA_OTHER' in side['seasons']
        assert 'ThreeDays_HA_EVENT' in side['seasons']
        assert 'ThreeDays_HA_BEFORE' in side['seasons']
        assert 'ThreeDays_HA_AFTER' in side['seasons']

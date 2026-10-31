from copy import deepcopy
from datetime import datetime, timezone, timedelta
import pytest
from conftest import base_tariff
from pwe_under_test.advance import build_advance_tariff
from pwe_under_test.tariff import ExportError, tariff_matches


def rate_at(tariff, moment, selling=False):
    side=tariff['sell_tariff'] if selling else tariff
    md=(moment.month,moment.day)
    minute=moment.hour*60+moment.minute
    found=[]
    for key,season in side['seasons'].items():
        if (season['fromMonth'],season['fromDay']) <= md <= (season['toMonth'],season['toDay']):
            for label,block in season['tou_periods'].items():
                for p in block['periods']:
                    a=p['fromHour']*60+p['fromMinute']
                    b=p['toHour']*60+p['toMinute'] or 1440
                    if p["fromDayOfWeek"] <= moment.weekday() <= p["toDayOfWeek"] and a<=minute<b:
                        found.append(side['energy_charges'][key]['rates'][label])
    assert len(found)==1,(moment,found)
    return found[0]


def test_overlay_preserves_normal_prices_and_rates():
    original=base_tariff()
    before=deepcopy(original)
    start=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
    end=start+timedelta(hours=1)
    new=build_advance_tariff(original,start,end,'Europe/London')
    assert original==before
    for d in (25,26,27,31):
        for minute in range(1440):
            moment=datetime(2026,10,d,minute//60,minute%60)
            for selling in (False,True):
                expected=(2.0 if selling else 2.1) if d==26 and 1080<=minute<1140 else rate_at(original,moment,selling)
                assert rate_at(new,moment,selling)==expected
    assert not tariff_matches(original,new)


def test_preserves_distinct_weekday_and_weekend_tariffs():
    t=base_tariff()
    for side in (t, t['sell_tariff']):
        season=side['seasons']['All year']
        for block in season['tou_periods'].values():
            for p in block['periods']:
                p['fromDayOfWeek']=0
                p['toDayOfWeek']=4
        # Saturday/Sunday entirely off-peak, unlike weekdays.
        season['tou_periods']['OFF_PEAK']['periods'].append({
            'fromDayOfWeek':5,'toDayOfWeek':5,'fromHour':0,'fromMinute':0,'toHour':0,'toMinute':0})
        season['tou_periods']['OFF_PEAK']['periods'].append({
            'fromDayOfWeek':6,'toDayOfWeek':6,'fromHour':0,'fromMinute':0,'toHour':0,'toMinute':0})
    original=deepcopy(t)
    start=datetime(2026,10,26,18,tzinfo=timezone.utc)
    new=build_advance_tariff(t,start,start+timedelta(hours=1),'Europe/London')
    assert t==original
    for d in (24,25,26,27,31):
        for minute in range(1440):
            moment=datetime(2026,10,d,minute//60,minute%60)
            for sell in (False,True):
                expect=(2.0 if sell else 2.10) if d==26 and 1080<=minute<1140 else rate_at(t,moment,sell)
                assert rate_at(new,moment,sell)==expect


def test_refuses_gapped_weekday_tariff():
    t=base_tariff()
    t['seasons']['All year']['tou_periods']['ON_PEAK']['periods'][0]['toDayOfWeek']=5
    with pytest.raises(ExportError, match='24 hours'):
        build_advance_tariff(t,datetime(2026,10,26,18,tzinfo=timezone.utc),datetime(2026,10,26,19,tzinfo=timezone.utc),'Europe/London')


@pytest.mark.asyncio
async def test_prepare_preview_no_write(engine,api):
    api.now=datetime(2026,10,26,16,0,tzinfo=timezone.utc)
    api.live_data['timestamp']=api.now.isoformat()
    start=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
    result=await engine.prepare(start,start+timedelta(hours=1))
    assert result['dry_run'] and not result['written']
    assert api.audit==[]
    assert engine.snapshot is None


@pytest.mark.asyncio
async def test_prepare_write_restore_tariff_only(engine,api):
    api.now=datetime(2026,10,26,16,0,tzinfo=timezone.utc)
    original=deepcopy(api.tariff_data)
    api.live_data['timestamp']=api.now.isoformat()
    start=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
    result=await engine.prepare(start,start+timedelta(hours=1),dry_run=False)
    assert result['written']
    assert api.audit==['save','tariff']
    assert engine.status=='prepared'
    api.now=datetime(2026,10,26,17,0,tzinfo=timezone.utc)
    await engine.tick()
    assert engine.status=='prepared'
    api.now=datetime(2026,10,26,19,0,tzinfo=timezone.utc)
    await engine.tick()
    assert engine.snapshot is None
    assert api.tariff_data==original
    assert not any(x.startswith(('mode:','reserve:','grid:')) for x in api.audit)

@pytest.mark.asyncio
async def test_prepare_write_failure_restores_original(engine,api):
    api.now=datetime(2026,10,26,16,0,tzinfo=timezone.utc)
    api.live_data['timestamp']=api.now.isoformat()
    original=deepcopy(api.tariff_data)
    api.failures['tariff']=1
    start=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
    with pytest.raises(ExportError,match='Original tariff restored'):
        await engine.prepare(start,start+timedelta(hours=1),dry_run=False)
    assert api.tariff_data==original
    assert engine.snapshot is None
    assert not any(x.startswith(('mode:','reserve:','grid:')) for x in api.audit)

@pytest.mark.asyncio
async def test_prepare_refuses_non_savings_mode(engine,api):
    api.now=datetime(2026,10,26,16,0,tzinfo=timezone.utc)
    api.live_data['timestamp']=api.now.isoformat()
    api.info_data['default_real_mode']='self_consumption'
    start=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
    with pytest.raises(ExportError,match='Savings'):
        await engine.prepare(start,start+timedelta(hours=1),dry_run=False)
    assert api.audit==[]


@pytest.mark.asyncio
async def test_missing_grid_state_allows_readonly_and_advance(engine,api):
    api.now=datetime(2026,10,26,16,0,tzinfo=timezone.utc)
    api.live_data['timestamp']=api.now.isoformat()
    del api.info_data['components']['disallow_charge_from_grid_with_solar_installed']
    result=await engine.check()
    assert result['read_only'] is True
    assert api.audit == []
    start=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
    result=await engine.prepare(start,start+timedelta(hours=1),dry_run=False)
    assert result['written'] is True
    assert api.audit==['save','tariff']
    await engine.stop()
    assert engine.snapshot is None
    assert api.audit==['save','tariff','tariff','save']

@pytest.mark.asyncio
async def test_missing_grid_state_recovery_loads_advance_snapshot(engine,api):
    api.now=datetime(2026,10,26,16,0,tzinfo=timezone.utc)
    api.live_data['timestamp']=api.now.isoformat()
    del api.info_data['components']['disallow_charge_from_grid_with_solar_installed']
    start=datetime(2026,10,26,18,0,tzinfo=timezone.utc)
    await engine.prepare(start,start+timedelta(hours=1),dry_run=False)
    snap=deepcopy(api.saved[-1])
    engine.load(snap)
    assert engine.snapshot['tariff_only'] is True


@pytest.mark.asyncio
async def test_prepare_optional_grid_block_restores_original_after_end(engine, api):
    api.now = datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc)
    api.live_data["timestamp"] = api.now.isoformat()
    begin = api.now + timedelta(minutes=30)
    finish = begin + timedelta(minutes=15)
    original = deepcopy(api.tariff_data)
    result = await engine.prepare(begin, finish, dry_run=False, block_grid_charging=True)
    assert result["written"] is True
    assert result["grid_charging_blocked"] is True
    assert api.info_data["components"]["disallow_charge_from_grid_with_solar_installed"] is True
    assert api.audit == ["save", "tariff", "grid:True"]
    api.now = finish
    await engine.tick()
    assert engine.snapshot is None
    assert api.tariff_data == original
    assert api.info_data["components"]["disallow_charge_from_grid_with_solar_installed"] is False
    assert api.audit == ["save", "tariff", "grid:True", "tariff", "grid:False", "save"]


@pytest.mark.asyncio
async def test_prepare_grid_block_requires_known_original_setting(engine, api):
    api.now = datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc)
    api.live_data["timestamp"] = api.now.isoformat()
    del api.info_data["components"]["disallow_charge_from_grid_with_solar_installed"]
    begin = api.now + timedelta(minutes=30)
    with pytest.raises(ExportError, match="Cannot read grid charging"):
        await engine.prepare(begin, begin + timedelta(minutes=15), dry_run=False, block_grid_charging=True)
    assert api.audit == []


@pytest.mark.asyncio
async def test_prepare_outside_final_hour_preview_only(engine, api):
    api.now = datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc)
    api.live_data["timestamp"] = api.now.isoformat()
    begin = api.now + timedelta(minutes=90)
    result = await engine.prepare(begin, begin + timedelta(minutes=15), dry_run=True, block_grid_charging=True)
    assert result["dry_run"] is True
    with pytest.raises(ExportError, match="final 60 minutes"):
        await engine.prepare(begin, begin + timedelta(minutes=15), dry_run=False, block_grid_charging=True)
    assert api.audit == []

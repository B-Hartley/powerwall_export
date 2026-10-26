from copy import deepcopy
from datetime import timedelta
import asyncio

import pytest

from pwe_under_test.engine import Engine
from pwe_under_test.tariff import ExportError, tariff_matches
from conftest import no_sleep

pytestmark = pytest.mark.asyncio


async def test_check_is_read_only(engine, api):
    result = await engine.check()
    assert result['read_only'] is True and result['backup_reserve_percent'] == 25
    assert api.audit == [] and engine.snapshot is None


async def test_default_reserve_unchanged_and_original_restored(engine, api):
    original_tariff, original_info = deepcopy(api.tariff_data), deepcopy(api.info_data)
    result = await engine.start()
    assert api.audit[0] == 'save'
    assert result['reserve_percent'] == 25 and not result['actual_export_confirmed']
    assert not any(x.startswith('reserve:') for x in api.audit)
    assert api.info_data['components']['disallow_charge_from_grid_with_solar_installed'] is True
    assert engine.status == 'active'
    await engine.stop()
    assert engine.snapshot is None and api.saved[-1]['snapshot'] is None
    assert tariff_matches(api.tariff_data, original_tariff)
    assert api.info_data == original_info
    assert engine.last_snapshot['original']['reserve'] == 25


async def test_override_temporary_and_restored(engine, api):
    await engine.start(reserve_percent=10)
    assert api.info_data['backup_reserve_percent'] == 10
    await engine.stop()
    assert api.info_data['backup_reserve_percent'] == 25


async def test_no_override_does_not_clobber_manual_reserve_change(engine, api):
    await engine.start()
    api.info_data['backup_reserve_percent'] = 35
    await engine.stop()
    assert api.info_data['backup_reserve_percent'] == 35


async def test_prevents_overlapping_export(engine, api):
    await engine.start()
    saved = deepcopy(engine.snapshot)
    with pytest.raises(ExportError, match='already pending'):
        await engine.start()
    assert engine.snapshot == saved


async def test_stop_is_idempotent(engine, api):
    assert (await engine.stop())['restored']
    assert not api.audit


async def test_disk_failure_prevents_all_device_writes(engine, api):
    api.save_failure = True
    with pytest.raises(ExportError, match='No settings'):
        await engine.start()
    assert api.audit == ['save'] and engine.snapshot is None


async def test_read_tariff_failure_prevents_writes(engine, api):
    api.failures['read_tariff'] = 1
    with pytest.raises(ExportError):
        await engine.start()
    assert not api.audit


async def test_missing_export_permission_prevents_writes(engine, api):
    api.info_data['components']['customer_preferred_export_rule'] = 'pv_only'
    with pytest.raises(ExportError, match='Everything'):
        await engine.start()
    assert not api.audit


@pytest.mark.parametrize('field,value', [('grid_status','Inactive'), ('storm_mode_active',True), ('storm_mode_active',None), ('grid_services_active',True)])
async def test_unsafe_live_status_refuses_export(engine, api, field, value):
    api.live_data[field] = value
    with pytest.raises(ExportError):
        await engine.start()
    assert not api.audit


async def test_stale_live_data_refuses_export(engine, api):
    api.live_data['timestamp'] = (api.now - timedelta(minutes=10)).isoformat()
    with pytest.raises(ExportError, match='stale'):
        await engine.start()
    assert not api.audit


async def test_unknown_grid_charging_refuses_export(engine, api):
    del api.info_data['components']['disallow_charge_from_grid_with_solar_installed']
    with pytest.raises(ExportError, match='grid-charging'):
        await engine.start()
    assert not api.audit


async def test_charge_already_at_reserve_refuses_export(engine, api):
    api.live_data['percentage_charged'] = 25
    with pytest.raises(ExportError, match='already at'):
        await engine.start()
    assert not api.audit


@pytest.mark.parametrize('reserve', [-1,101,float('nan'),float('inf'),True])
async def test_invalid_reserve(engine, api, reserve):
    with pytest.raises(ExportError):
        await engine.start(reserve_percent=reserve)
    assert not api.audit


async def test_timeout_after_write_restores(engine, api):
    original = deepcopy(api.tariff_data)
    api.after_failure = 'tariff'
    with pytest.raises(ExportError, match='Original settings restored'):
        await engine.start()
    assert engine.snapshot is None
    assert tariff_matches(api.tariff_data, original)


async def test_start_mode_failure_restores(engine, api):
    api.failures['mode'] = 1
    with pytest.raises(ExportError):
        await engine.start()
    assert engine.snapshot is None
    assert api.info_data['default_real_mode'] == 'autonomous'


async def test_failed_restore_keeps_snapshot_and_does_not_reenable_grid_charging(engine, api):
    await engine.start(reserve_percent=10)
    api.failures['tariff'] = 1
    with pytest.raises(ExportError, match='NOT confirmed'):
        await engine.stop()
    assert engine.snapshot and engine.status == 'restore_pending'
    assert api.info_data['default_real_mode'] == 'self_consumption'
    assert api.info_data['backup_reserve_percent'] == 25
    assert api.info_data['components']['disallow_charge_from_grid_with_solar_installed'] is True
    assert api.saved[-1]['snapshot'] is not None
    await engine.stop()
    assert engine.snapshot is None


async def test_retry_eventually_restores(engine, api):
    await engine.start()
    api.failures['tariff'] = 1
    with pytest.raises(ExportError):
        await engine.stop()
    api.now = engine.next_retry + timedelta(seconds=1)
    await engine.tick()
    assert engine.snapshot is None


async def test_completion_disk_failure_retains_recovery(engine, api):
    await engine.start()
    api.save_failure = True
    with pytest.raises(ExportError, match='saving recovery'):
        await engine.stop()
    assert engine.snapshot is not None
    api.save_failure = False
    await engine.stop()
    assert engine.snapshot is None


async def test_restart_restores_even_before_end(engine, api):
    await engine.start(reserve_percent=10)
    second = Engine(api, api.save, '123456', 'Europe/London', now=lambda: api.now, sleep=no_sleep)
    second.load(deepcopy(api.saved[-1]))
    assert second.status == 'restore_pending'
    await second.stop('restart')
    assert second.snapshot is None and api.info_data['backup_reserve_percent'] == 25


async def test_recovery_site_mismatch_rejected(engine):
    with pytest.raises(ExportError, match='different energy site'):
        engine.load({'snapshot': {'site_id': '98765'}, 'last_snapshot': None})


async def test_scheduled_end_restores(engine, api):
    await engine.start(duration_minutes=5)
    api.now += timedelta(minutes=6)
    await engine.tick()
    assert engine.snapshot is None


async def test_reaching_reserve_restores(engine, api):
    await engine.start()
    api.live_data['percentage_charged'] = 25
    await engine.tick()
    assert engine.snapshot is None


async def test_grid_failure_during_export_restores(engine, api):
    await engine.start()
    api.live_data['grid_status'] = 'Inactive'
    await engine.tick()
    assert engine.snapshot is None


async def test_three_monitoring_failures_restore(engine, api):
    await engine.start()
    api.failures['read_live'] = 3
    await engine.tick(); await engine.tick()
    assert engine.snapshot is not None
    await engine.tick()
    assert engine.snapshot is None


async def test_monitor_differentiates_export_from_accepted_settings(engine, api):
    await engine.start()
    api.live_data['grid_power'] = 200
    await engine.tick()
    assert 'not currently observed' in engine.message
    api.live_data['grid_power'] = -2000
    await engine.tick()
    assert 'export observed' in engine.message


async def test_readback_mismatch_not_reported_as_success(engine, api):
    api.ignore_tariff_writes = True
    with pytest.raises(ExportError):
        await engine.start()
    assert engine.status != 'active'


async def test_time_zone_mismatch_no_writes(engine, api):
    api.info_data['installation_time_zone'] = 'America/New_York'
    with pytest.raises(ExportError, match='time zones differ'):
        await engine.start()
    assert not api.audit


async def test_no_nested_temporary_snapshot(engine, api):
    api.tariff_data['code'] = 'HA-PWE-TEMPORARY'
    with pytest.raises(ExportError, match='already installed'):
        await engine.start()
    assert not api.audit


async def test_explicit_end_overrides_duration_and_rounds_down(engine, api):
    end = api.now + timedelta(minutes=18, seconds=35)
    result = await engine.start(duration_minutes=60, end_time=end)
    assert result['end_at'] == (api.now + timedelta(minutes=18)).isoformat()


async def test_check_during_export_is_blocked(engine, api):
    await engine.start()
    with pytest.raises(ExportError, match='restoring'):
        await engine.check()


async def test_concurrent_start_does_not_replace_snapshot(engine, api):
    results = await asyncio.gather(engine.start(), engine.start(), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, ExportError) for r in results) == 1
    assert sum(x == 'save' for x in api.audit) == 1


async def test_original_self_powered_mode_restored(engine, api):
    api.info_data['default_real_mode'] = 'self_consumption'
    await engine.start()
    await engine.stop()
    assert api.info_data['default_real_mode'] == 'self_consumption'

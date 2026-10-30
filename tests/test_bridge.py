"""Adapter contract tests with minimal module stubs, NOT a Home Assistant runtime."""
import enum
import importlib
import sys
import types
from unittest.mock import AsyncMock

import pytest

from pwe_under_test.tariff import ExportError
from conftest import base_tariff


@pytest.fixture
def bridge(monkeypatch):
    class State(enum.Enum):
        LOADED = 'loaded'
        NOT_LOADED = 'not_loaded'
    home = types.ModuleType('homeassistant'); home.__path__=[]
    entries = types.ModuleType('homeassistant.config_entries'); entries.ConfigEntryState=State
    core = types.ModuleType('homeassistant.core'); core.HomeAssistant=object
    tesla = types.ModuleType('tesla_fleet_api'); tesla.__path__=[]
    const = types.ModuleType('tesla_fleet_api.const')
    class Method(enum.StrEnum):
        GET = 'GET'
    const.Method = Method
    for key,value in [('homeassistant',home),('homeassistant.config_entries',entries),('homeassistant.core',core),('tesla_fleet_api',tesla),('tesla_fleet_api.const',const)]:
        monkeypatch.setitem(sys.modules,key,value)
    sys.modules.pop('pwe_under_test.bridge',None)
    module=importlib.import_module('pwe_under_test.bridge')
    yield module,State
    sys.modules.pop('pwe_under_test.bridge',None)


def make_hass(state):
    api = types.SimpleNamespace(site_info=AsyncMock(return_value={'response':{'value':1}}),
                                time_of_use_settings=AsyncMock(return_value={'response':{'code':201}}),
                                grid_import_export=AsyncMock(return_value={'response':{'code':201}}),
                                _request=AsyncMock(return_value={'response':base_tariff()}))
    site=types.SimpleNamespace(id=123456,api=api,device={'name':'Home'},info_coordinator=types.SimpleNamespace(data={'components_battery':True}))
    entry=types.SimpleNamespace(entry_id='parent',domain='tessie',state=state.LOADED,runtime_data=types.SimpleNamespace(energysites=[site]))
    entries={'parent':entry}
    hass=types.SimpleNamespace(config_entries=types.SimpleNamespace(async_get_entry=lambda key:entries.get(key),async_entries=lambda domain:[v for v in entries.values() if v.domain==domain]))
    return hass,entry,site,api,entries


@pytest.mark.asyncio
async def test_reuses_exact_client_and_re_resolves_after_reload(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    client=module.SharedClient(hass,'parent','123456')
    assert await client.info()=={'value':1}
    replacement=types.SimpleNamespace(site_info=AsyncMock(return_value={'response':{'value':2}}))
    site.api=replacement
    assert await client.info()=={'value':2}
    assert api.site_info.await_count==1


@pytest.mark.asyncio
async def test_tariff_rate_uses_parent_request_auth(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    client=module.SharedClient(hass,'parent','123456')
    assert await client.tariff()==base_tariff()
    api._request.assert_awaited_once_with('GET','api/1/energy_sites/123456/tariff_rate')


@pytest.mark.asyncio
async def test_embedded_tariff_does_not_override_live_get(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    client=module.SharedClient(hass,'parent','123456')
    embedded={'tou_settings':{'tariff_content_v2':{'seasons':{'broken':{}},'energy_charges':{'broken':{}}}}}
    assert await client.tariff(embedded)==base_tariff()
    api._request.assert_awaited_once_with('GET','api/1/energy_sites/123456/tariff_rate')


@pytest.mark.asyncio
async def test_write_passes_tariff_not_double_wrapped(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    client=module.SharedClient(hass,'parent','123456')
    tariff=base_tariff()
    await client.set_tariff(tariff)
    api.time_of_use_settings.assert_awaited_once_with(tariff)


@pytest.mark.asyncio
async def test_grid_write_never_nulls_export_rule(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    client=module.SharedClient(hass,'parent','123456')
    await client.grid(True,'battery_ok')
    api.grid_import_export.assert_awaited_once_with(disallow_charge_from_grid_with_solar_installed=True,customer_preferred_export_rule='battery_ok')


@pytest.mark.parametrize('body',[{'response':{'result':False}},{'response':{'code':400}},{'error':'failure'},{'response':{}},None])
def test_negative_or_unknown_ack_rejected(bridge,body):
    module,_=bridge
    with pytest.raises(ExportError):
        module.SharedClient._ack(body)


@pytest.mark.parametrize('body',[{'response':{'result':True}},{'response':{'code':201}}, {'response':True}])
def test_positive_ack_accepted(bridge,body):
    module,_=bridge
    module.SharedClient._ack(body)


@pytest.mark.asyncio
async def test_no_secret_leak_from_exception(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    api.site_info.side_effect=RuntimeError('Authorization: Bearer SUPER_SECRET')
    with pytest.raises(ExportError) as exc:
        await module.SharedClient(hass,'parent','123456').info()
    assert 'SUPER_SECRET' not in str(exc.value)
    assert 'RuntimeError' in str(exc.value)


def test_discovery_lists_energy_sites_not_cars(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    result=module.discover_sites(hass)
    assert result['parent:123456'].domain=='tessie'
    entry.state=state.NOT_LOADED
    assert module.discover_sites(hass)=={}


@pytest.mark.asyncio
async def test_parent_unavailable_fails_closed(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    entry.state=state.NOT_LOADED
    with pytest.raises(ExportError,match='not loaded'):
        await module.SharedClient(hass,'parent','123456').info()
    api.site_info.assert_not_awaited()

@pytest.mark.asyncio
async def test_write_http_status_sanitized_and_classified(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    class Forbidden(RuntimeError):
        status = 403
    api.time_of_use_settings.side_effect=Forbidden('Authorization: Bearer SUPER_SECRET')
    with pytest.raises(ExportError, match='HTTP 403') as err:
        await module.SharedClient(hass,'parent','123456').set_tariff(base_tariff())
    assert 'SUPER_SECRET' not in str(err.value)
    assert 'permission' in str(err.value)


@pytest.mark.asyncio
async def test_write_http_status_unavailable_does_not_leak(bridge):
    module,state=bridge
    hass,entry,site,api,entries=make_hass(state)
    api.time_of_use_settings.side_effect=RuntimeError('Authorization: Bearer SUPER_SECRET')
    with pytest.raises(ExportError, match='status unavailable') as err:
        await module.SharedClient(hass,'parent','123456').set_tariff(base_tariff())
    assert 'SUPER_SECRET' not in str(err.value)

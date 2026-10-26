"""Load the pure controller without installing or pretending to run Home Assistant."""
from copy import deepcopy
from datetime import datetime, timezone
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'custom_components' / 'powerwall_export'
namespace = types.ModuleType('pwe_under_test')
namespace.__path__ = [str(PACKAGE)]
sys.modules['pwe_under_test'] = namespace

from pwe_under_test.engine import Engine
from pwe_under_test.tariff import ExportError, normalize_tariff


def base_tariff():
    def period(a, b):
        return {'fromDayOfWeek': 0, 'toDayOfWeek': 6, 'fromHour': a, 'toHour': b, 'fromMinute': 0, 'toMinute': 0}
    season = {'fromMonth': 1, 'fromDay': 1, 'toMonth': 12, 'toDay': 31,
              'tou_periods': {'OFF_PEAK': {'periods': [period(0, 5)]}, 'ON_PEAK': {'periods': [period(5, 0)]}}}
    side = {'version': 1, 'name': 'Original Octopus example', 'code': 'ORIGINAL', 'utility': 'Octopus example',
            'currency': 'GBP', 'seasons': {'All year': season},
            'energy_charges': {'ALL': {'rates': {'ALL': 0}}, 'All year': {'rates': {'OFF_PEAK': 0.07, 'ON_PEAK': 0.30}}},
            'demand_charges': {'ALL': {'rates': {'ALL': 0}}, 'All year': {'rates': {}}}}
    result = deepcopy(side)
    result['sell_tariff'] = deepcopy(side)
    result['sell_tariff']['energy_charges']['All year']['rates'] = {'OFF_PEAK': 0.04, 'ON_PEAK': 0.15}
    return normalize_tariff(result)


class FakeAPI:
    def __init__(self):
        self.now = datetime(2026, 9, 11, 17, 0, tzinfo=timezone.utc)
        self.tariff_data = base_tariff()
        self.info_data = {'default_real_mode': 'autonomous', 'backup_reserve_percent': 25,
                          'installation_time_zone': 'Europe/London',
                          'components': {'battery': True, 'customer_preferred_export_rule': 'battery_ok',
                                         'disallow_charge_from_grid_with_solar_installed': False}}
        self.live_data = {'percentage_charged': 80, 'grid_status': 'Active', 'storm_mode_active': False,
                          'grid_services_active': False, 'grid_power': -2500, 'battery_power': 3000,
                          'timestamp': self.now.isoformat()}
        self.audit = []
        self.failures = {}
        self.after_failure = None
        self.saved = []
        self.ignore_tariff_writes = False
        self.save_failure = False

    def failure(self, name):
        remaining = self.failures.get(name, 0)
        if remaining:
            self.failures[name] = remaining - 1
            raise ExportError(f'Synthetic failure: {name}')

    async def info(self):
        self.failure('read_info')
        return deepcopy(self.info_data)

    async def tariff(self, info=None):
        self.failure('read_tariff')
        return deepcopy(self.tariff_data)

    async def live(self):
        self.failure('read_live')
        return deepcopy(self.live_data)

    async def set_tariff(self, value):
        self.audit.append('tariff')
        self.failure('tariff')
        if not self.ignore_tariff_writes:
            self.tariff_data = deepcopy(value)
        if self.after_failure == 'tariff':
            self.after_failure = None
            raise ExportError('Synthetic timeout after successful write')

    async def mode(self, value):
        self.audit.append(f'mode:{value}')
        self.failure('mode')
        self.info_data['default_real_mode'] = value

    async def reserve(self, value):
        self.audit.append(f'reserve:{value}')
        self.failure('reserve')
        self.info_data['backup_reserve_percent'] = value

    async def grid(self, disallow, export_rule):
        self.audit.append(f'grid:{disallow}')
        self.failure('grid')
        self.info_data['components']['disallow_charge_from_grid_with_solar_installed'] = disallow
        self.info_data['components']['customer_preferred_export_rule'] = export_rule

    async def save(self, data):
        self.audit.append('save')
        if self.save_failure:
            raise OSError('Synthetic disk full')
        self.saved.append(deepcopy(data))


async def no_sleep(_seconds):
    return


@pytest.fixture
def api():
    return FakeAPI()


@pytest.fixture
def engine(api):
    return Engine(api, api.save, '123456', 'Europe/London', now=lambda: api.now, sleep=no_sleep)

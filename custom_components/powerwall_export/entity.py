"""Common local state for the companion entities."""
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, VERSION


class PowerwallExportEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime, key, name):
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.site_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, runtime.site_id)},
                                           name="Powerwall Export", manufacturer="Custom integration",
                                           model="Timed tariff controller", sw_version=VERSION)

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, self.runtime.signal, self._changed))

    @callback
    def _changed(self):
        self.async_write_ha_state()

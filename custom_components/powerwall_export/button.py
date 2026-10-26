"""Three simple device-page buttons. Custom duration/reserve use service actions."""
from homeassistant.components.button import ButtonEntity
from .entity import PowerwallExportEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PowerwallExportButton(entry.runtime_data, action, name, icon) for action, name, icon in (
        ("check_setup", "Check setup (read only)", "mdi:clipboard-check-outline"),
        ("start_export", "Export for 30 minutes", "mdi:battery-arrow-up"),
        ("stop_export", "Stop and restore", "mdi:restore"),
    )])


class PowerwallExportButton(PowerwallExportEntity, ButtonEntity):
    def __init__(self, runtime, action, name, icon):
        super().__init__(runtime, action, name)
        self.action = action
        self._attr_icon = icon

    async def async_press(self):
        await self.runtime.run(self.action)

"""Local controller status, not a duplicate Powerwall telemetry integration."""
from homeassistant.components.sensor import SensorEntity
from .entity import PowerwallExportEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PowerwallExportStatus(entry.runtime_data)])


class PowerwallExportStatus(PowerwallExportEntity, SensorEntity):
    _attr_icon = "mdi:battery-clock-outline"

    def __init__(self, runtime):
        super().__init__(runtime, "status", "Status")

    @property
    def native_value(self):
        return self.runtime.engine.status

    @property
    def extra_state_attributes(self):
        engine = self.runtime.engine
        snap = engine.snapshot or {}
        return {
            "message": engine.message,
            "restoration_pending": engine.snapshot is not None,
            "scheduled_end": snap.get("end_at"),
            "target_reserve_percent": snap.get("target_reserve"),
            "original_reserve_percent": snap.get("original", {}).get("reserve"),
            "temporary_reserve_override": snap.get("override_reserve", False),
            "grid_power_w": engine.last_live.get("grid_power"),
            "battery_power_w": engine.last_live.get("battery_power"),
            "live_data_timestamp": engine.last_live.get("timestamp"),
            "last_setup_check": engine.last_check or None,
        }

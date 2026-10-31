"""Powerwall Export: timed tariff control using a parent's authenticated client."""
from __future__ import annotations

from datetime import datetime
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .runtime import Runtime
from .tariff import ExportError

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
BASE = {vol.Required("config_entry_id"): cv.string}
PREPARE_SCHEMA = vol.Schema({**BASE, vol.Required("start_time"): cv.string,
    vol.Required("end_time"): cv.string, vol.Optional("dry_run", default=True): cv.boolean})
START_SCHEMA = vol.Schema({**BASE,
    vol.Optional("duration_minutes", default=30): vol.All(vol.Coerce(int), vol.Range(min=5, max=360)),
    vol.Optional("reserve_percent"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("end_time"): cv.string,
})


async def async_setup(hass, config):
    async def handle(call):
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        runtime = getattr(entry, "runtime_data", None) if entry else None
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED or not isinstance(runtime, Runtime):
            raise HomeAssistantError("Select a loaded Powerwall Export integration entry.")
        kwargs = {}
        if call.service in ("start_export", "prepare_session"):
            if call.service == "prepare_session":
                kwargs = {k: v for k, v in call.data.items() if k != "config_entry_id"}
                for key in ("start_time", "end_time"):
                    try:
                        kwargs[key] = datetime.fromisoformat(kwargs[key].replace("Z", "+00:00"))
                    except ValueError:
                        raise HomeAssistantError("Session timestamps must be ISO datetimes with time zones.") from None
        if call.service == "start_export":
            kwargs = {k: v for k, v in call.data.items() if k != "config_entry_id"}
            if "end_time" in kwargs:
                try:
                    kwargs["end_time"] = datetime.fromisoformat(kwargs["end_time"].replace("Z", "+00:00"))
                except ValueError:
                    raise HomeAssistantError("End time must be an ISO timestamp including UTC offset or Z.") from None
        result = await runtime.run(call.service, **kwargs)
        return result if call.return_response else None

    for name in ("check_setup", "start_export", "stop_export", "prepare_session", "get_tariff", "reconcile_recovery", "confirm_recovery"):
        hass.services.async_register(DOMAIN, name, handle,
                                     schema=START_SCHEMA if name == "start_export" else PREPARE_SCHEMA if name == "prepare_session" else vol.Schema(BASE),
                                     supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass, entry):
    runtime = Runtime(hass, entry)
    try:
        await runtime.load()
    except ExportError as exc:
        raise ConfigEntryError(str(exc)) from None
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.start_watchdog()
    return True


async def async_unload_entry(hass, entry):
    if not await entry.runtime_data.unload():
        return False
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

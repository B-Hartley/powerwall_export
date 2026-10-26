"""Choose an already authenticated energy site; no credential flow."""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers import selector

from .bridge import discover_sites
from .const import CONF_SITE, CONF_SOURCE_ENTRY, CONF_TIME_ZONE, DOMAIN


class PowerwallExportConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        sites = discover_sites(self.hass)
        if not sites:
            return self.async_abort(reason="no_energy_sites")
        errors = {}
        if user_input is not None:
            choice = sites.get(user_input["source"])
            if choice is None:
                errors["base"] = "source_unavailable"
            else:
                zone = user_input[CONF_TIME_ZONE]
                try:
                    ZoneInfo(zone)
                    if choice.time_zone and choice.time_zone != zone:
                        errors[CONF_TIME_ZONE] = "time_zone_mismatch"
                except (ZoneInfoNotFoundError, ValueError):
                    errors[CONF_TIME_ZONE] = "invalid_time_zone"
                if not errors:
                    # One controller per physical site, even when both sources see it.
                    await self.async_set_unique_id(choice.site_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=f"Powerwall Export - {choice.name}", data={
                        CONF_SOURCE_ENTRY: choice.entry_id, CONF_SITE: choice.site_id,
                        CONF_TIME_ZONE: zone, "source_domain": choice.domain,
                    })
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("source", default=next(iter(sites))): selector.SelectSelector({
                "options": [{"value": key, "label": f"{item.name} - {item.domain} (site {item.site_id})"} for key, item in sites.items()],
                "mode": "dropdown",
            }),
            vol.Required(CONF_TIME_ZONE, default=self.hass.config.time_zone): str,
        }), errors=errors)

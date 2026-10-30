"""Small compatibility boundary for HA Core 2026.9.1's authenticated clients.

No reading, copying, storing, refreshing or logging of tokens. Resolve the
parent runtime on EVERY request so a parent reload does not leave a stale client.
The private _request callable is needed only for the observed tariff_rate GET.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import SOURCE_DOMAINS
from .tariff import ExportError, extract_tariff


@dataclass(frozen=True)
class SiteChoice:
    key: str
    entry_id: str
    domain: str
    site_id: str
    name: str
    time_zone: str | None


def discover_sites(hass: HomeAssistant) -> dict[str, SiteChoice]:
    choices = {}
    for domain in SOURCE_DOMAINS:
        for entry in hass.config_entries.async_entries(domain):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            runtime = getattr(entry, "runtime_data", None)
            for site in getattr(runtime, "energysites", ()):
                api, site_id = getattr(site, "api", None), str(getattr(site, "id", ""))
                if not site_id.isdecimal() or api is None:
                    continue
                coordinator = getattr(site, "info_coordinator", None)
                info = getattr(coordinator, "data", {}) or {}
                # Both parent integrations flatten the cached site_info dictionary.
                if info.get("components_battery") is False:
                    continue
                device = getattr(site, "device", {}) or {}
                name = str(device.get("name") or info.get("site_name") or "Powerwall")
                key = f"{entry.entry_id}:{site_id}"
                choices[key] = SiteChoice(key, entry.entry_id, domain, site_id, name,
                                          info.get("installation_time_zone") or None)
    return choices


class SharedClient:
    def __init__(self, hass: HomeAssistant, source_entry_id: str, site_id: str):
        self.hass, self.source_entry_id, self.site_id = hass, source_entry_id, site_id

    def _api(self) -> Any:
        entry = self.hass.config_entries.async_get_entry(self.source_entry_id)
        if entry is None or entry.domain not in SOURCE_DOMAINS or entry.state is not ConfigEntryState.LOADED:
            raise ExportError("The selected Tessie/Tesla Fleet connection is not loaded. Recovery will retry when it is available.")
        for site in getattr(getattr(entry, "runtime_data", None), "energysites", ()):
            if str(getattr(site, "id", "")) == self.site_id:
                api = getattr(site, "api", None)
                if api is not None:
                    return api
        raise ExportError("The selected energy site is unavailable in the parent integration.")

    async def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        api = self._api()
        method = getattr(api, name, None)
        if not callable(method):
            raise ExportError("The parent's API-client layout has changed; this companion needs an update.")
        try:
            async with asyncio.timeout(40):
                return await method(*args, **kwargs)
        except ExportError:
            raise
        except Exception as exc:
            # Never expose third-party exception text: it can contain bearer
            # tokens or request URLs. Expose only validated numeric HTTP status.
            status = getattr(exc, "status", None)
            if status is None:
                status = getattr(exc, "status_code", None)
            if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
                category = {400: "bad-request/payload", 401: "authentication",
                            403: "scope or energy-product permission",
                            404: "endpoint or site unavailable",
                            409: "conflicting request",
                            422: "tariff validation",
                            429: "rate limit"}.get(status, "server/transport" if status >= 500 else "HTTP error")
                raise ExportError(f"{name}: HTTP {status} ({category}); {type(exc).__name__}.") from None
            raise ExportError(f"{name}: {type(exc).__name__}; HTTP status unavailable. Check Tessie and Tesla command logs.") from None

    @staticmethod
    def _read_response(response: Any) -> dict:
        if not isinstance(response, dict) or response.get("error"):
            raise ExportError("Tesla returned an invalid read response.")
        body = response.get("response", response)
        if not isinstance(body, dict) or body.get("error"):
            raise ExportError("Tesla did not return readable energy-site data.")
        return body

    @staticmethod
    def _ack(response: Any) -> None:
        if not isinstance(response, dict) or response.get("error"):
            raise ExportError("Tesla rejected the write or returned an invalid acknowledgement.")
        body = response.get("response", response)
        if body is True:
            return
        if not isinstance(body, dict) or body.get("error") or body.get("result") is False:
            raise ExportError("Tesla rejected the write.")
        code = body.get("code")
        if code is not None:
            try:
                if 200 <= int(code) < 300:
                    return
            except (ValueError, TypeError):
                pass
            raise ExportError("Tesla returned an unsuccessful command code.")
        if body.get("result") is True:
            return
        raise ExportError("Tesla returned an unrecognised write acknowledgement; read-back/recovery is required.")

    async def info(self) -> dict:
        return self._read_response(await self._call("site_info"))

    async def live(self) -> dict:
        return self._read_response(await self._call("live_status"))

    async def tariff(self, info: dict | None = None) -> dict:
        # Always read the same tariff_rate representation used by Get current
        # tariff. Embedded site_info sometimes has a different/partial season
        # schema, causing setup checks to reject a tariff that diagnostics can
        # read. Never substitute an unverified cached tariff for a live read.
        # This remains an undocumented endpoint: fail closed if unavailable.
        from tesla_fleet_api.const import Method
        response = await self._call("_request", Method.GET, f"api/1/energy_sites/{self.site_id}/tariff_rate")
        return extract_tariff(self._read_response(response))

    async def set_tariff(self, value: dict) -> None:
        self._ack(await self._call("time_of_use_settings", value))

    async def mode(self, value: str) -> None:
        self._ack(await self._call("operation", value))

    async def reserve(self, value: float) -> None:
        self._ack(await self._call("backup", value))

    async def grid(self, disallow: bool, export_rule: str) -> None:
        # Pass BOTH known values: the library otherwise serialises None for the
        # second parameter. We never change the existing 'Everything' permission.
        self._ack(await self._call("grid_import_export",
                                  disallow_charge_from_grid_with_solar_installed=disallow,
                                  customer_preferred_export_rule=export_rule))

"""HA persistence, timers, notifications and service exception handling."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.components import persistent_notification
from homeassistant.core import CoreState, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval

from .bridge import SharedClient
from .const import CONF_SITE, CONF_SOURCE_ENTRY, CONF_TIME_ZONE, DOMAIN
from .engine import Engine
from .recovery import RecoveryFile
from .tariff import ExportError

_LOGGER = logging.getLogger(__name__)


class Runtime:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.signal = f"{DOMAIN}_{entry.entry_id}"
        self.site_id = entry.data[CONF_SITE]
        # Site-based key survives removing/re-adding the config entry. Recovery
        # data is deliberately never deleted automatically on integration removal.
        self.store = RecoveryFile(hass.config.path(".storage", f"{DOMAIN}.site_{self.site_id}.json"))
        self.engine = Engine(SharedClient(hass, entry.data[CONF_SOURCE_ENTRY], self.site_id),
                             self._save_recovery, self.site_id, entry.data[CONF_TIME_ZONE], self.changed)
        self.cancel_deadline = None
        self.deadline_value = None
        self.cancel_interval = None
        self.last_notification = None

    async def _save_recovery(self, data):
        await self.hass.async_add_executor_job(self.store.write, data)

    async def load(self):
        self.engine.load(await self.hass.async_add_executor_job(self.store.read))

    @callback
    def changed(self):
        async_dispatcher_send(self.hass, self.signal)
        snap = self.engine.snapshot
        end_at = snap["end_at"] if snap else None
        if end_at != self.deadline_value:
            if self.cancel_deadline:
                self.cancel_deadline()
            self.cancel_deadline = None
            self.deadline_value = end_at
            if end_at:
                deadline = datetime.fromisoformat(end_at)
                # If expired, recovery is already pending; interval/startup handles it.
                if deadline > self.engine.now():
                    self.cancel_deadline = async_track_point_in_utc_time(self.hass, self._deadline, deadline)
        if self.engine.status == "restore_pending" and self.engine.message != self.last_notification:
            self.last_notification = self.engine.message
            persistent_notification.async_create(
                self.hass, self.engine.message + " Do not remove this integration or its .storage recovery file until restored.",
                title="Powerwall Export: restoration needs attention", notification_id=self.signal)
        elif not snap and self.last_notification:
            persistent_notification.async_dismiss(self.hass, self.signal)
            self.last_notification = None

    async def start_watchdog(self):
        self.cancel_interval = async_track_time_interval(self.hass, self._tick, timedelta(seconds=60))
        self.changed()
        if self.engine.snapshot:
            # Conservative reboot policy: restore immediately, never resume selling.
            try:
                await self.engine.stop("Home Assistant restart or integration reload")
            except ExportError:
                self.changed()

    async def _deadline(self, _now):
        try:
            await self.engine.stop("scheduled end")
        except ExportError:
            self.changed()

    async def _tick(self, _now):
        try:
            await self.engine.tick()
        except Exception as exc:
            # Avoid raw third-party exception text, which can contain credentials.
            _LOGGER.error("Powerwall export watchdog failed (%s)", type(exc).__name__)

    async def run(self, action: str, **kwargs):
        try:
            if action == "get_tariff":
                # Existing Tessie/Fleet authenticated client; never writes settings.
                from .tariff import extract_tariff
                tariff = extract_tariff(await self.engine.api.tariff())
                return {"tariff": tariff, "read_only": True}
            if action == "check_setup":
                result = await self.engine.check()
            elif action == "start_export":
                if self.hass.state is not CoreState.running:
                    raise ExportError("Home Assistant must be fully running before starting an export.")
                result = await self.engine.start(**kwargs)
            elif action == "prepare_session":
                if self.hass.state is not CoreState.running:
                    raise ExportError("Home Assistant must be running before preparing a session.")
                result = await self.engine.prepare(**kwargs)
            else:
                result = await self.engine.stop()
            return result
        except ExportError as exc:
            # Errors raised by our code have deliberately sanitised messages.
            persistent_notification.async_create(self.hass, str(exc), title="Powerwall Export", notification_id=f"{self.signal}_action")
            raise HomeAssistantError(str(exc)) from None
        except Exception as exc:
            raise HomeAssistantError(f"Powerwall Export failed ({type(exc).__name__}). Check the parent integration and recovery status.") from None

    async def unload(self) -> bool:
        if self.engine.snapshot:
            try:
                await self.engine.stop("integration unloading")
            except Exception:
                self.changed()
                return False
        if self.cancel_interval:
            self.cancel_interval()
        if self.cancel_deadline:
            self.cancel_deadline()
        return True

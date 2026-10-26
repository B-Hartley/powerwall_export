"""Testable export/restore transaction; deliberately independent of Home Assistant."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Protocol

from .tariff import ExportError, build_export_tariff, normalize_tariff, number, tariff_matches
from .advance import build_advance_tariff


class Transport(Protocol):
    async def info(self) -> dict: ...
    async def tariff(self, info: dict | None = None) -> dict: ...
    async def live(self) -> dict: ...
    async def set_tariff(self, value: dict) -> None: ...
    async def mode(self, value: str) -> None: ...
    async def reserve(self, value: float) -> None: ...
    async def grid(self, disallow: bool, export_rule: str) -> None: ...


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def settings(info: dict, require_grid: bool = False) -> dict:
    components = info.get("components", {})
    if not isinstance(components, dict) or components.get("battery") is not True:
        raise ExportError("The selected energy site does not report a battery.")
    mode = info.get("default_real_mode")
    if mode not in ("self_consumption", "autonomous", "backup"):
        raise ExportError("Cannot read a restorable operating mode.")
    reserve = number(info.get("backup_reserve_percent"), "Current backup reserve")
    rule = components.get("customer_preferred_export_rule")
    if rule != "battery_ok":
        raise ExportError("Select Energy Exports: Everything in the Tesla app first.")
    disallow = components.get("disallow_charge_from_grid_with_solar_installed")
    if not isinstance(disallow, bool):
        if require_grid:
            raise ExportError("Cannot read the grid-charging setting; immediate export refused. Advance scheduling does not require it.")
        disallow = None
    return {"mode": mode, "reserve": reserve, "export_rule": rule, "disallow_grid_charge": disallow}


def safe_live(live: dict, now: datetime) -> float:
    if live.get("grid_status") != "Active":
        raise ExportError("Grid connection is not confirmed active.")
    if live.get("storm_mode_active") is not False:
        raise ExportError("Storm Watch is active or its status is unknown.")
    if live.get("grid_services_active") is not False:
        raise ExportError("A grid-services event is active or its status is unknown.")
    try:
        stamp = datetime.fromisoformat(live["timestamp"].replace("Z", "+00:00"))
        age = (now - stamp).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise ExportError("Live status has no valid timestamp.") from exc
    if not -60 <= age <= 180:
        raise ExportError("Powerwall live data is stale; refusing to rely on it.")
    return number(live.get("percentage_charged"), "Battery charge")


class Engine:
    def __init__(self, api: Transport, save: Callable[[dict], Awaitable[None]],
                 site_id: str, time_zone: str, changed: Callable[[], None] = lambda: None,
                 now: Callable[[], datetime] = utcnow,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.api, self.save, self.site_id, self.time_zone = api, save, site_id, time_zone
        self.changed, self.now, self.sleep = changed, now, sleep
        self.lock = asyncio.Lock()
        self.snapshot: dict | None = None
        self.last_snapshot: dict | None = None
        self.status, self.message = "idle", "No export requested."
        self.last_live: dict = {}
        self.last_check: dict = {}
        self.live_failures = 0
        self.next_retry = self.now()
        self.retry_count = 0

    def load(self, data: dict | None) -> None:
        if data is None:
            return
        if not isinstance(data, dict):
            raise ExportError("Saved recovery data is invalid; do not delete it.")
        if "snapshot" not in data or "last_snapshot" not in data:
            raise ExportError("Saved recovery journal is incomplete; manual recovery required.")
        snap = data.get("snapshot")
        if snap is not None:
            if not isinstance(snap, dict) or str(snap.get("site_id")) != self.site_id:
                raise ExportError("Saved recovery data belongs to a different energy site.")
            for key in ("original", "tariff", "end_at", "started_at", "target_reserve", "override_reserve"):
                if key not in snap:
                    raise ExportError("Saved recovery data is incomplete; manual recovery required.")
            original = snap["original"]
            if (snap.get("schema") != 1 or not isinstance(original, dict)
                or original.get("mode") not in ("self_consumption", "autonomous", "backup")
                or original.get("export_rule") != "battery_ok"
                or (not snap.get("tariff_only") and not isinstance(original.get("disallow_grid_charge"), bool))
                or not isinstance(snap["override_reserve"], bool)):
                raise ExportError("Recovery settings are invalid; manual recovery required.")
            number(original.get("reserve"), "Saved reserve")
            number(snap["target_reserve"], "Saved target reserve")
            normalize_tariff(snap["tariff"])
            try:
                for key in ("started_at", "end_at"):
                    if datetime.fromisoformat(snap[key]).tzinfo is None:
                        raise ValueError("Missing time zone")
            except (ValueError, TypeError):
                raise ExportError("Recovery timestamps are invalid; manual recovery required.") from None
            self.snapshot = snap
            self.status = "restore_pending"
            self.message = "Interrupted session: restoring instead of resuming export."
        self.last_snapshot = data.get("last_snapshot")

    async def _persist(self) -> None:
        await self.save(deepcopy({"snapshot": self.snapshot, "last_snapshot": self.last_snapshot}))

    def _state(self, state: str, message: str) -> None:
        self.status, self.message = state, message
        self.changed()

    async def _read(self, require_grid: bool = False, strict_live: bool = False) -> tuple[dict, dict, dict, dict]:
        info = await self.api.info()
        original = settings(info, require_grid=require_grid)
        tariff = normalize_tariff(await self.api.tariff(info))
        if str(tariff.get("code", "")).startswith("HA-PWE-") or str(tariff.get("name", "")).startswith("HA temporary export"):
            raise ExportError("A temporary export tariff is already installed. Restore it before starting another session.")
        live = await self.api.live()
        if strict_live:
            safe_live(live, self.now())
        return info, original, tariff, live

    async def check(self) -> dict:
        async with self.lock:
            if self.snapshot:
                raise ExportError("Finish restoring the existing session before running setup checks.")
            info, original, tariff, live = await self._read()
            self.last_live = live
            self.last_check = {"checked_at": self.now().isoformat(), "site_id": self.site_id,
                               "mode": original["mode"], "backup_reserve_percent": original["reserve"],
                               "battery_percent": live["percentage_charged"], "export_rule": original["export_rule"],
                               "tariff_name": tariff.get("name", "Unnamed tariff"), "time_zone": self.time_zone,
                               "tariff_readable": True, "read_only": True}
            self._state("ready", "Read-only checks passed. No Powerwall settings changed; write permission is not yet tested.")
            return deepcopy(self.last_check)

    async def prepare(self, start_time: datetime, end_time: datetime, dry_run: bool = True) -> dict:
        """Save a future one-day TOU window, leaving mode, reserve and grid alone."""
        async with self.lock:
            if self.snapshot:
                raise ExportError("Another export or recovery is pending. Restore it first.")
            now = self.now()
            if start_time.tzinfo is None or end_time.tzinfo is None:
                raise ExportError("Calendar timestamps must include their UTC offset.")
            start = start_time.astimezone(timezone.utc)
            end = end_time.astimezone(timezone.utc)
            if not timedelta(minutes=10) <= start - now <= timedelta(days=2):
                raise ExportError("Session must start 10 minutes to 48 hours from now.")
            if not timedelta(minutes=1) <= end - start <= timedelta(hours=6):
                raise ExportError("Session duration must be between 1 minute and 6 hours.")
            info, original, tariff, live = await self._read()
            if original["mode"] != "autonomous":
                raise ExportError("Advance test requires Powerwall already in Savings / Time-Based Control. No changes made.")
            reported_zone = info.get("installation_time_zone")
            if reported_zone and reported_zone != self.time_zone:
                raise ExportError("Powerwall time zone does not match integration configuration.")
            temporary = build_advance_tariff(tariff, start, end, self.time_zone)
            response = {"start": start.isoformat(), "end": end.isoformat(),
                        "original_tariff": tariff.get("name"), "mode_unchanged": True,
                        "reserve_unchanged": True, "grid_charging_unchanged": True,
                        "temporary_buy_price_gbp_per_kwh": 2.10,
                        "temporary_sell_price_gbp_per_kwh": 2.00,
                        "dry_run": bool(dry_run), "written": False}
            if dry_run:
                self._state("ready", "Future tariff preview succeeded; NO changes sent to Powerwall.")
                return response
            if live.get("grid_services_active") is True or live.get("storm_mode_active") is True:
                raise ExportError("An active grid-services or Storm Watch event prevents tariff preparation.")
            if live.get("grid_status") != "Active":
                raise ExportError("Grid is not confirmed active; refusing to prepare a tariff.")
            snap = {"schema": 1, "site_id": self.site_id, "started_at": now.isoformat(),
                    "end_at": end.isoformat(), "original": original, "tariff": tariff,
                    "temporary_tariff": temporary, "target_reserve": original["reserve"],
                    "override_reserve": False, "time_zone": self.time_zone,
                    "tariff_only": True, "event_start_at": start.isoformat()}
            self.snapshot = snap
            try:
                await self._persist()
            except Exception as exc:
                self.snapshot = None
                raise ExportError("Could not save original tariff. Nothing written.") from exc
            self._state("starting", "Original tariff saved. Writing prepared future session.")
            try:
                await self.api.set_tariff(temporary)
                for attempt in range(4):
                    if tariff_matches(await self.api.tariff(), temporary):
                        break
                    if attempt == 3:
                        raise ExportError("Prepared tariff read-back mismatch.")
                    await self.sleep(3)
                # Verify that only tariff changed; no other control methods are called.
                current = settings(await self.api.info())
                if current != original:
                    raise ExportError("Powerwall operating settings unexpectedly changed.")
                self._state("prepared", "Future tariff confirmed; Savings mode, reserve and grid settings unchanged. Check Tesla app schedule.")
                response.update({"dry_run": False, "written": True})
                return response
            except (Exception, asyncio.CancelledError) as exc:
                self._state("restore_pending", "Prepared tariff failed verification; restoring original.")
                restored = await self._restore_unlocked("preparation failed")
                raise ExportError("Preparation failed. " + ("Original tariff restored." if restored else "Restoration pending; check Tesla app.")) from exc

    async def start(self, duration_minutes: int = 30, reserve_percent: float | None = None,
                    end_time: datetime | None = None) -> dict:
        async with self.lock:
            if self.snapshot:
                raise ExportError("An export/recovery is already pending. Stop and restore it first.")
            if reserve_percent is not None:
                reserve_percent = number(reserve_percent, "Temporary backup reserve")
            now = self.now()
            end = end_time if end_time is not None else now + timedelta(minutes=duration_minutes)
            if end.tzinfo is None:
                raise ExportError("End time must include a UTC offset or Z.")
            end = end.astimezone(timezone.utc).replace(second=0, microsecond=0)
            if not 120 <= (end - now).total_seconds() <= 21600:
                raise ExportError("End time must be at least two minutes and at most six hours from now.")
            info, original, tariff, live = await self._read(require_grid=True, strict_live=True)
            reported_zone = info.get("installation_time_zone")
            if reported_zone and reported_zone != self.time_zone:
                raise ExportError("Powerwall and configured time zones differ. Re-add the integration with the Powerwall time zone.")
            target = original["reserve"] if reserve_percent is None else reserve_percent
            if live["percentage_charged"] <= target:
                raise ExportError("Battery is already at or below the requested reserve.")
            temporary = build_export_tariff(now, end, self.time_zone, tariff["currency"])
            snap = {"schema": 1, "site_id": self.site_id, "started_at": now.isoformat(), "end_at": end.isoformat(),
                    "original": original, "tariff": tariff, "temporary_tariff": temporary,
                    "target_reserve": target, "override_reserve": reserve_percent is not None,
                    "time_zone": self.time_zone}
            self.snapshot = snap
            try:
                # Durable before ANY write, including a request that times out after succeeding.
                await self._persist()
            except Exception as exc:
                self.snapshot = None
                self._state("error", "Could not save recovery data. No settings were changed.")
                raise ExportError(self.message) from exc
            self._state("starting", "Original settings saved; applying temporary export settings.")
            try:
                # Never enable cheap-price grid charging under a synthetic tariff.
                await self.api.grid(True, original["export_rule"])
                await self.api.set_tariff(temporary)
                await self.api.mode("autonomous")
                if reserve_percent is not None:
                    await self.api.reserve(target)
                await self._verify(temporary, "autonomous", True, target if reserve_percent is not None else None)
                if self.now() >= end:
                    raise ExportError("Session ended while settings were being applied.")
                self.last_live, self.live_failures = live, 0
                self._state("active", "Temporary settings confirmed. Check actual grid export; discharge is not guaranteed.")
                return {"end_at": end.isoformat(), "reserve_percent": target, "settings_confirmed": True,
                        "actual_export_confirmed": False}
            except (Exception, asyncio.CancelledError) as exc:
                self._state("restore_pending", "Start was interrupted or failed; restoring original settings.")
                restored = await self._restore_unlocked("start failed")
                raise ExportError("Export did not start cleanly. " + ("Original settings restored." if restored else "Restoration is pending; check the Tesla app and Home Assistant notification.")) from exc

    async def _verify(self, tariff: dict, mode: str, disallow: bool, reserve: float | None) -> None:
        last = "Read-back does not match requested settings."
        for attempt in range(4):
            try:
                info = await self.api.info()
                actual_tariff = await self.api.tariff(info)
                components = info.get("components", {})
                good = (tariff_matches(actual_tariff, tariff)
                        and info.get("default_real_mode") == mode
                        and components.get("disallow_charge_from_grid_with_solar_installed") is disallow
                        and components.get("customer_preferred_export_rule") == "battery_ok")
                if reserve is not None:
                    value = number(info.get("backup_reserve_percent"), "Read-back reserve")
                    good = good and abs(value - reserve) < 0.11
                if good:
                    return
            except ExportError as exc:
                last = str(exc)
            if attempt < 3:
                await self.sleep(3)
        raise ExportError(last)

    async def stop(self, reason: str = "manual stop") -> dict:
        async with self.lock:
            if not self.snapshot:
                return {"restored": True, "message": "No pending export. No settings changed."}
            if not await self._restore_unlocked(reason):
                raise ExportError(self.message)
            return {"restored": True}

    async def _restore_unlocked(self, reason: str) -> bool:
        snap = self.snapshot
        if not snap:
            return True
        original, tariff = snap["original"], snap["tariff"]
        self._state("restoring", f"Restoring original settings: {reason}.")
        failures: list[str] = []
        if snap.get("tariff_only"):
            # Never touch mode, grid charging or reserve for an advance-only session.
            tariff_ok = False
            try:
                await self.api.set_tariff(tariff)
                for attempt in range(4):
                    if tariff_matches(await self.api.tariff(), tariff):
                        tariff_ok = True
                        break
                    if attempt < 3:
                        await self.sleep(3)
            except Exception:
                pass
            if not tariff_ok:
                failures.append("original tariff read-back")
            else:
                try:
                    if settings(await self.api.info()) != original:
                        failures.append("operating settings changed externally")
                except Exception:
                    failures.append("operating settings read-back")
        else:
            # Stop TBC first. Do not restore grid charging or TBC until normal prices
            # have been read back. Never disable Storm Watch or change export limits.
            try:
                await self.api.mode("self_consumption")
            except Exception:
                failures.append("temporary self-powered mode")
            if snap["override_reserve"]:
                try:
                    await self.api.reserve(original["reserve"])
                except Exception:
                    failures.append("backup reserve")
            tariff_ok = False
            try:
                await self.api.set_tariff(tariff)
                for attempt in range(4):
                    if tariff_matches(await self.api.tariff(), tariff):
                        tariff_ok = True
                        break
                    if attempt < 3:
                        await self.sleep(3)
            except Exception:
                pass
            if not tariff_ok:
                failures.append("original tariff verification")
            else:
                try:
                    await self.api.grid(original["disallow_grid_charge"], original["export_rule"])
                except Exception:
                    failures.append("grid charging")
                try:
                    await self.api.mode(original["mode"])
                except Exception:
                    failures.append("operating mode")
                try:
                    await self._verify(tariff, original["mode"], original["disallow_grid_charge"],
                                       original["reserve"] if snap["override_reserve"] else None)
                    # Exact final read-back wins over transient errors on individual writes.
                    failures = []
                except Exception:
                    failures.append("final read-back")
        if not failures:
            previous_archive = self.last_snapshot
            self.last_snapshot = {**snap, "restored_at": self.now().isoformat(), "restore_reason": reason}
            self.snapshot = None
            try:
                await self._persist()
            except Exception:
                # Never declare recovery finished if its durable record was not updated.
                self.snapshot, self.last_snapshot = snap, previous_archive
                failures.append("saving recovery completion")
        if failures:
            self.retry_count += 1
            self.next_retry = self.now() + timedelta(seconds=min(60 * 2 ** min(self.retry_count - 1, 4), 900))
            self._state("restore_pending", "Restoration NOT confirmed: " + ", ".join(failures) + ". Snapshot retained; automatic retries continue while this integration is running. Check the Tesla app.")
            return False
        self.retry_count = 0
        self._state("idle", "Original tariff and changed settings restored and read back successfully.")
        return True

    async def tick(self) -> None:
        # Do not queue periodic work behind a long start/restore transaction.
        if self.lock.locked() or not self.snapshot:
            return
        async with self.lock:
            if not self.snapshot:
                return
            now = self.now()
            if self.status == "restore_pending":
                if now >= self.next_retry:
                    await self._restore_unlocked("automatic recovery retry")
                return
            if now >= datetime.fromisoformat(self.snapshot["end_at"]):
                await self._restore_unlocked("scheduled end")
                return
            if self.snapshot.get("tariff_only"):
                event_start = datetime.fromisoformat(self.snapshot["event_start_at"])
                self._state("prepared" if now < event_start else "active",
                            "Prepared tariff installed; no mode/reserve/grid changes. Export is not guaranteed.")
                return
            try:
                live = await self.api.live()
                self.last_live = live
                try:
                    charge = safe_live(live, now)
                except ExportError:
                    await self._restore_unlocked("unsafe, stale, or unavailable live status")
                    return
                self.live_failures = 0
                if charge <= self.snapshot["target_reserve"]:
                    await self._restore_unlocked("backup reserve reached")
                    return
                exporting = (number(live.get("grid_power"), "Grid power", -1000000, 1000000) < -100
                             and number(live.get("battery_power"), "Battery power", -1000000, 1000000) > 100)
                self._state("active", "Battery discharge and net grid export observed." if exporting
                            else "Temporary tariff active, but battery discharge AND net grid export are not currently observed.")
            except Exception:
                self.live_failures += 1
                if self.live_failures >= 3:
                    await self._restore_unlocked("three consecutive monitoring failures")
                else:
                    self._state("active", "Live monitoring failed; actual export cannot currently be confirmed.")

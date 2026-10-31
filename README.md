# Powerwall Export v0.1.4 (experimental)

A small Home Assistant companion integration for timed, tariff-driven Powerwall
export. Uses your **existing Tessie or Tesla Fleet authenticated energy-site
client**. No new Tesla application, login, access token, subscription or secrets
file is introduced by this integration. Your selected parent's existing service
requirements, permissions and any API charges still apply.

**Target:** Home Assistant Core 2026.9.1; user's Powerwall 2 installation.
Source interfaces were checked against that exact Core release and its pinned
`tesla-fleet-api` 1.10.0. **This is an experimental first release, not live-tested
inside Home Assistant or against a physical Powerwall.** The included automated
tests use a simulated Powerwall and minimal adapter stubs. A supervised first
test is essential before enabling the Octopus automation.

See [requirements](REQUIREMENTS.md), [HACS install](INSTALL_HACS.md), and [advance session tests](PREPARE_AHEAD_TEST.md).

## What you get

Three buttons on a new **Powerwall Export** device:

- **Check setup (read only)**: reads the current tariff, operating settings and
  live status. Changes nothing on the Powerwall. Does not prove write permission.
- **Export for 30 minutes**: saves original settings, applies a temporary export
  tariff and Time-Based Control, then restores. Keeps the existing reserve.
- **Stop and restore**: ends this override and restores the original tariff and
  changed settings. Your original tariff may itself allow ordinary battery
  export; this is not a permanent prohibition on all export.

A **Status** sensor shows the controller state, scheduled end, reserve, last
telemetry timestamp and an explanatory message. `active` means the override was
accepted and read back, NOT proof that your meter is exporting. Actual export
and battery-discharge observations are reported separately in the message.

The **Start timed export** action supports any duration from 5 to 360 minutes,
an optional temporary reserve, or an explicit session end timestamp. End times
are rounded **down** to the minute. An explicit end must be at least two minutes
and no more than six hours away. It takes precedence over duration.

The included **Octopus blueprint** uses BottlecapDave's existing calendar; it
is not another Octopus integration and does not need your account number.

## Installation

1. Make a Home Assistant backup. Unzip this archive on a computer.
2. Copy `custom_components/powerwall_export` into your Home Assistant
   configuration directory's `custom_components` folder. The result must be:

   ```text
   /config/custom_components/powerwall_export/manifest.json
   /config/custom_components/powerwall_export/__init__.py
   ...all the other files and the translations folder...
   ```

   `/config` means the directory containing `configuration.yaml`; the host path
   can differ with your installation. Do not put the whole archive directory
   inside `custom_components`, and do not replace your existing Tessie/Fleet files.

3. To use the Octopus blueprint later, also copy the supplied blueprint to:

   ```text
   /config/blueprints/automation/powerwall_export/octopus_saving_session.yaml
   ```

4. Restart Home Assistant. Go to **Settings > Devices & services > Add
   integration**, search **Powerwall Export**, and select it. Refresh the browser
   if the new integration does not appear immediately after the restart.
5. Choose your energy site **through Tessie** where available, or Tesla Fleet.
   Only one companion controller can be added per physical energy site, even
   when it appears through both connections. Check the time zone is
   **Europe/London**, or exactly the installation time zone reported by Tesla.

No `configuration.yaml` entry is needed. The repository layout supports a HACS custom-repository install. See INSTALL_HACS.md. Installing/adding it does not initiate export.
If re-adding a site with unfinished recovery data, it attempts restoration.

## First test: do this before using the Octopus blueprint

Disable any other automations/scripts which change Powerwall tariffs, mode,
reserve or grid charging. Two integrations merely monitoring the site are fine;
two controllers issuing conflicting writes are not.

Take screenshots in the Tesla app of the normal tariff, operating mode, backup
reserve and grid-charging setting. Check **Energy Exports = Everything** remains
available. The integration refuses to enable permission that was not already
set. It never changes installer export limits, Storm Watch or grid contactors.

Press **Check setup (read only)**. The Status sensor should become `ready`.
Any failure here means **do not proceed**; record the exact error. The integration
requires readable buy AND sell tariff content so it does not replace an unknown
original profile. Some installations may need a compatibility adjustment.

For the first actual test, open **Developer tools > Actions** (labelled Tools
in some frontend layouts), choose **Powerwall Export: Start timed export**, select
the new Powerwall Export connection, set **Duration = 5**, and leave **Temporary
backup reserve** and **End time** disabled/omitted. Perform the action while
watching the Tesla app and your usual live power sensors.

Verify both battery discharge and net grid export, not just a changed tariff.
At the end, verify the original tariff, mode and grid-charging preference return.
You can also use **Stop and restore** to stop the test early. Only enable automatic
Saving Sessions after both a timed end and manual stop have worked correctly.

## Keeping or changing the reserve

The default never writes the backup-reserve setting. With no override, the
controller uses the reserve read at the start of the session. Tesla still
controls its own battery protection and reserve behaviour.

For one session only, enable **Temporary backup reserve** in the Start timed
export action and enter the desired percentage. Example: 10 means a 10% reserve
for that session; the originally read reserve is restored afterwards. A lower
reserve means less stored energy available during an outage. Zero is accepted
only when explicitly requested, never chosen by default.

The 30-minute button always keeps the existing reserve. The Octopus blueprint
has a separate **Override backup reserve** switch, off by default.

## Octopus Saving Sessions / Power Down

After the manual tests succeed, open **Settings > Automations & scenes >
Blueprints**, find **Powerwall Export - joined Octopus Saving Session**, and
create an automation from it.

Choose your BottlecapDave **Power Down** calendar, normally:

```text
calendar.octopus_energy_YOUR_ACCOUNT_ID_octoplus_power_down
```

An older installation may instead use the **Saving Sessions** calendar ending
in `_octoplus_saving_sessions`. Choose the actual entity from the picker.
**Do not select Power Up or Free Electricity.** Choose your new Powerwall Export
connection, leave the reserve override off initially, and save/enable the automation.

The calendar contains sessions your account has joined. Join the session in
Octopus or use your existing joining automation; this blueprint does not join it.
At calendar start, it sends the event's end timestamp to the controller. The
controller owns the restoration timer; the automation is not a long-running delay.
No action occurs merely because this blueprint file was copied into place.

This first version deliberately does not resume an interrupted session after
a Home Assistant restart, catch up a missed calendar start, merge overlapping
sessions or update an already-running event if Octopus changes its duration.
A second start while an export/recovery is pending is rejected. Disable the
blueprint for manually scheduled tests or conflicting events.

## Restoration and safety behaviour

Before the first write, the controller atomically saves and synchronises a
recovery journal to disk. Write failures propagate, so a failed snapshot save
prevents all device changes. The journal retains the original tariff, original
mode, grid-charging preference, reserve and session deadline. It contains no
authentication tokens.

While exporting, grid charging is temporarily disabled. The temporary tariff
uses synthetic import/export prices to influence Tesla's optimiser. These are
not Octopus prices and may distort Tesla app cost/savings displays. The code
does not change your Octopus contract, but altered battery behaviour can affect
your energy costs. Grid charging is restored only after the normal tariff has
been read back.

At stop, the controller requests Self-Powered mode first, restores the tariff,
then restores the original mode and grid-charging preference. It restores the
reserve only if this session explicitly changed it. It compares returned rates,
schedules and billing fields rather than trusting an HTTP success alone.

If restoration is unconfirmed, it retains the snapshot, posts a Home Assistant
notification and retries with backoff while the integration is running. After
restart/reload it attempts to restore, not resume. Monitoring also attempts
restoration when the reserve is reached, the grid becomes unavailable, an active
Storm Watch/grid-services event is observed, live data becomes stale, or live
monitoring repeatedly fails. It does not disable Tesla's own protections.

There is a timer for the exact rounded end and a one-minute watchdog. Live
monitoring adds one read per minute only while an export is active; tariff/info
reads and writes are also made during checks, transitions and recovery.

The high-export-price interval is restricted to the session's calendar dates
and times, rather than repeating every day. **This is only an additional pricing
backstop, not guaranteed offline restoration.** Annual tariff seasons do not
encode a year, so the same date/time could recur next year if left in place.
Clock-change/DST-transition windows are rejected rather than guessed.

## Important limits and recovery

There is no fixed-kW discharge command here. Tesla firmware, its optimiser,
reserve, site export constraints and service availability determine the result.
A changed tariff cannot guarantee export or an Octopus reward. Site-level commands
apply to all Powerwalls at that selected energy site, not an individual battery.

The companion accesses another integration's **internal runtime client**. That
is not an officially stable extension API. Tariff read-back falls back to the
observed `GET /tariff_rate` endpoint when `site_info` lacks the tariff; that read
endpoint is not documented in Tesla's public energy-endpoint list. Parent/Core,
firmware or cloud changes may require an update. If read-back fails, starting
fails closed. Normal tariff writes use the parent's `time_of_use_settings` method.

**Home Assistant, the selected parent integration and the Tesla/Tessie cloud
must be available to restore settings.** A power/internet/cloud outage may leave
the temporary tariff, disabled grid charging or a lower reserve in place until
recovery. Supervise the first tests; do not regard this as unattended-production
software based solely on the included tests.

If recovery fails, check the Tesla app immediately. Use **Self-Powered** mode to
remove the tariff-driven discharge instruction, restore a suitable backup
reserve, and restore the original rate plan/settings from your screenshots.
Do not turn grid charging back on under the synthetic cheap-price tariff.
Keep the companion enabled so it can retry; its intended final target is the
original saved configuration. If you make emergency changes, monitor subsequent
automatic recovery so those changes are not unexpectedly replaced by the saved
original settings.

The private journal is:

```text
/config/.storage/powerwall_export.site_ENERGY_SITE_ID.json
```

Do not delete, share or edit it casually. It retains the active snapshot until
verification succeeds and keeps one last completed snapshot for recovery. It is
not automatically deleted on integration removal. Corrupt files are retained
and block new starts. When an active restoration fails, normal unloading is
refused; forcibly deleting the files disables recovery.

## Testing and sources

Run the included offline tests from this directory with Python 3.11+ and
`pytest`, `pytest-asyncio` installed:

```bash
python -m pytest -q tests
python -m compileall -q custom_components/powerwall_export
```

See `TEST_REPORT.md` for the executed checks and their limits. No real Home
Assistant instance, Powerwall, Tesla account or Octopus account was accessed.
See `SOURCES.md` for the primary implementation references.

## Diagnose unsupported weekday ranges (read-only)

With your existing Tessie authenticated connection, open **Developer Tools > Actions > Powerwall Export: Get current tariff (read only)**, choose the Powerwall Export integration instance and run the action with response enabled. Copy the `tariff` response. It includes Tesla tariff rates and period structures but not Tessie/Tesla authentication credentials. You can redact tariff names if desired. It does not write settings and does not require Tesla Fleet.

**Note:** This diagnostic reads the current tariff and does not yet fix arbitrary weekday encoding. Never use a `set_tariff` action on an unreviewed profile.

## Supported Tesla tariff layouts (v0.1.6)

- **Octopus-linked rolling tariffs** with named `Yesterday`, `Today`, `Tomorrow`, `TwoDays`, `ThreeDays` seasons, including a season spanning the October year boundary.
- **Manually configured seasonal tariffs** such as `Summer`/`Winter`, including an empty placeholder Winter season and overnight `23:30` to `05:30` periods.
- Tesla day-of-week spans `0..6` and overnight intervals are expanded safely for the chosen session date. The original import and export tariff snapshots are preserved for restoration.

The first stage is still a **read-only preview**. Unknown or overlapping tariff structures are rejected rather than guessed. A linked Octopus tariff may be regenerated and replace the temporary schedule: monitor its read-back and never assume it remains effective. This change was tested offline against structural replicas of the two example tariffs, not against Tesla's live API.

### Protect evening battery charge during Saving Sessions

The `prepare_session` action accepts optional battery protection controls:

- `minimum_end_charge: 60` — desired battery percentage to retain at session end.
- `charge_margin: 5` — safety buffer, giving a default protected floor of 65%.
- `enforce_reserve: false` — opt in to temporarily raising Tesla's Backup Reserve to the protected floor; the original reserve is saved and restored afterwards.
- `block_grid_charging: false` — separately opt in to blocking grid charging temporarily, restoring its previous value at the end.

A live session is skipped unless the current battery percentage is more than 2 percentage points above the protected floor. Battery status is checked during preparation and by the watchdog (approximately once a minute). On reaching the floor or repeated monitoring failures, the integration requests early restoration; this is not instantaneous or a guaranteed hard cut-off. A dry run reports `eligible_to_start`, `battery_charge_percent`, and `protected_charge_percent` without writes. Tariff preparation remains limited to the final hour before the session.

The floor is a manually selected percentage, **not yet a forecast of evening demand or guaranteed future charging**. Do not reduce it on the assumption that an extra Intelligent Octopus cheap charging slot will materialise. Grid charging and reserve changes are only made when explicitly enabled.


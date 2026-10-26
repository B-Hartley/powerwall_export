# Powerwall Export v0.1.1 — advance Saving Session test

**Experimental, not tested on a live Powerwall or inside Home Assistant.** Built for HA Core 2026.9.1 and the user's Powerwall 2, reusing the authenticated Tessie or Tesla Fleet client. It is an update to v0.1.0, not a separate integration.

## What the test does

The script blueprint reads the **next upcoming joined Power Down / Saving Session** from BottlecapDave's calendar in the next 36 hours, including its actual start/end. It does not assume 18:00 and does not join sessions.

By default, the script calls `powerwall_export.prepare_session` with `dry_run: true`. This checks the tariff and builds a future overlay but **does not write anything**. The script creates a persistent notification showing the selected session and result.

After the preview succeeds, explicitly turn on **Write to Powerwall** in the blueprint's script configuration and run it again. The controller saves a recovery journal BEFORE writing. It then uploads a temporary tariff with synthetic import/export prices of **£2.10/£2.00 per kWh only for the session window**, verifies the tariff read-back, and schedules restoration of the original tariff at the event end. These are **optimisation signals, not Octopus prices**; Tesla may display misleading cost estimates. Actual export is **not guaranteed**. **The original rates outside the session are preserved.**

The advance test does **not** change operating mode, backup reserve, grid charging or Energy Exports permission. It requires the Powerwall to already be in Savings mode and Energy Exports = Everything. The original tariff is restored at the session end, or immediately via **Stop and restore**. On Home Assistant restart/reload, the recovery policy restores immediately rather than continuing the prepared window.

## Install/update

1. Back up Home Assistant and the existing v0.1.0 integration files. Disable the v0.1.0 Octopus automatic start blueprint (if enabled) to prevent conflicts.
2. Copy `custom_components/powerwall_export/` from this archive over the **existing** folder at `/config/custom_components/powerwall_export/` (or install it fresh if not already installed).
3. Copy `blueprints/script/powerwall_export/preview_next_joined_session.yaml` to the same path under `/config/blueprints/script/`.
4. Restart Home Assistant. Ensure the Powerwall Export integration is loaded.
5. Open **Settings → Automations & scenes → Blueprints → Script blueprints**, find **Powerwall Export - test next joined Octopus Saving Session**, and create a script. Choose the **joined Power Down** calendar and your **Powerwall Export** connection. Leave **Write to Powerwall = OFF**.
6. Run the script. Read the resulting Home Assistant notification. It should show `dry_run: true`, `written: false`, and the actual event times. If it fails, **do not enable writing**; send the exact error.
7. Only after reviewing the preview and taking screenshots of the Tesla app's current tariff, change the script's blueprint setting to **Write to Powerwall = ON** and run it again. Check the Tesla app immediately for the future window, confirm normal prices are unchanged outside it, and watch actual discharge/export at session start. Confirm original tariff is restored after the session.

The script selects the next upcoming event; if the calendar is empty or does not expose a joined event, it will NOT write. The script is manually run and does not create a recurring automation.

## Safety / limitations

- **Do not write** until `Check setup (read only)` and the advance preview succeed. The existing v0.1.0 5-minute test is still recommended, but the advance test can be previewed independently.
- The test overlay supports **one local calendar day** and a simple all-week tariff schedule (each period must apply Sunday–Saturday). Unusual Tesla tariff layouts, weekday/weekend variations, overlapping periods, DST transitions, and missing rate data are **rejected** rather than guessed.
- A successful preview does not prove the Tesla cloud will accept the write. A successful write does not guarantee discharge or an Octopus payment.
- An outage can prevent restoration. The tariff's elevated price is confined to the event's annual date and time, but the Powerwall may retain the temporary tariff until connectivity returns; the date pattern can recur next year. Check the Tesla app after the test.
- **Only one tariff-writing automation should be enabled.** Do not run the old v0.1.0 start-at-session blueprint simultaneously.
- An active recovery snapshot blocks subsequent attempts. If recovery is pending, use **Stop and restore**, monitor Home Assistant notifications, and use the Tesla app to restore manually if needed. Do not delete the recovery file.
- This update was exercised using simulated Powerwall clients and the original offline tests; it has **not been live tested**.

## Direct action alternative

Instead of the script blueprint, use Developer Tools → Actions → `powerwall_export.prepare_session` with your Powerwall Export config entry ID, an ISO-8601 `start_time`, `end_time`, and `dry_run: true`. This action does **not** read Octopus itself; the script blueprint performs the calendar lookup.


## v0.1.2 grid-charging fix

Read-only setup and advance tariff preview/write no longer require the Tesla site-info grid-charging flag. They never write grid settings. Immediate timed export still requires a readable grid-charging flag and refuses to start without one; no guessed restoration state. Your Home Assistant switch is not automatically used for that mode.

## v0.1.4 weekday/weekend support
The calendar overlay now accepts separate weekday/weekend time-of-use periods, checks for complete nonoverlapping coverage on each of the seven days, and changes only the event weekday on the event date. All other weekday and weekend prices are preserved. Run preview first; this patch has not been live-tested against Tesla's actual returned tariff format. A preview or write may still refuse unfamiliar layouts; do not bypass the refusal.

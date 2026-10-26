# Implementation references

Source interfaces inspected on 11 September 2026. This is independent custom
code, not an official Tesla, Tessie, Home Assistant or Octopus release.

## Home Assistant Core 2026.9.1

Both parent integrations expose energy-site clients through
`config_entry.runtime_data.energysites`, with each site's client in `.api`.
The companion re-resolves that object for every request and never extracts tokens.

- Tessie models: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/tessie/models.py
- Tessie setup: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/tessie/__init__.py
- Tesla Fleet models: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/tesla_fleet/models.py
- Tesla Fleet setup and OAuth callback: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/tesla_fleet/__init__.py
- Tessie pinned dependencies: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/tessie/manifest.json
- Tesla Fleet pinned dependencies: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/tesla_fleet/manifest.json
- Site-info field example: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/tests/components/tessie/fixtures/site_info.json
- Live-status field example: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/tests/components/tessie/fixtures/live_status.json

## Shared API library and tariff structure

- Pinned EnergySite class: https://raw.githubusercontent.com/Teslemetry/python-tesla-fleet-api/v1.10.0/tesla_fleet_api/tesla/energysite.py
- Tessie's reuse of the EnergySite class: https://raw.githubusercontent.com/Teslemetry/python-tesla-fleet-api/v1.10.0/tesla_fleet_api/tessie/__init__.py
- Tesla energy endpoints and tariff validation rules: https://developer.tesla.com/docs/fleet-api/endpoints/energy
- Tesla's canonical write-side tariff example: https://digitalassets-energy.tesla.com/raw/upload/app/fleet-api/example-tariff/PGE-EV2-A.json
- Existing project's observed tariff_rate read endpoint (not an official stability guarantee): https://raw.githubusercontent.com/vi-code/powerwall-export/master/core/fleet.py
- Existing project's observations about GET/POST normalisation: https://raw.githubusercontent.com/vi-code/powerwall-export/master/core/tariff.py

The code was written independently. Third-party project observations guide the
compatibility fallback, but are not proof it works with the user's firmware.

## UI, events and persistence

- Custom integration service actions: https://developers.home-assistant.io/docs/dev_101_services/
- Config flow: https://developers.home-assistant.io/docs/core/integration/config_flow/
- Manifest: https://developers.home-assistant.io/docs/creating_integration_manifest/
- Blueprint selectors: https://www.home-assistant.io/docs/blueprint/selectors/
- Octopus joined-session calendars: https://bottlecapdave.github.io/HomeAssistant-OctopusEnergy/entities/octoplus/
- Tesla export/reserve settings: https://www.tesla.com/en_gb/support/energy/powerwall/mobile-app/advanced-settings

The recovery journal intentionally uses a separate strict atomic file writer.
In Core 2026.9.1, Store's internal write handler logs certain write failures
without propagating them, and the loader may rename corrupt JSON and return
None. Those defaults do not meet this controller's fail-closed journal contract:
https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/helpers/storage.py

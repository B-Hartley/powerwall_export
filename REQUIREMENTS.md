# Requirements

- Home Assistant Core 2026.9.1 target; other versions have not been validated.
- Existing Tessie or Tesla Fleet Home Assistant integration with an authenticated Powerwall energy site.
- Tesla Powerwall with Energy Exports permission set to Everything; Tesla site in Time-Based Control/Savings mode for advance tariff preparation.
- A correctly readable current tariff, including import and export periods. Unknown formats refuse to write.
- For joined Octopus Saving Sessions, BottlecapDave's Octopus Energy Home Assistant integration and its joined Power Down calendar.
- Reliable internet connectivity for writes and restoration. Local HA availability alone is insufficient.
- A full Home Assistant backup and screenshots of the original Tesla app tariff, mode, reserve, and grid charging before any first write.

No new Tesla credentials are requested by this integration, but parent integration credentials and privileges are still required. The parent integrations are accessed through unsupported internal runtime APIs, and may change across versions.

## Restrictions

Do not operate a second controller that rewrites the same Powerwall tariff. Battery export is tariff-influenced rather than a guaranteed discharge command. The temporary tariff uses *synthetic* values and distorts Tesla app cost estimates. Firmware, grid services, reserve, export limitations and Tesla's optimiser control actual power flows. Review the detailed recovery and first-test instructions in README.md and PREPARE_AHEAD_TEST.md.

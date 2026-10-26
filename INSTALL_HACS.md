# Installing using HACS

This repository is structured as a Home Assistant **custom integration** for HACS, but it has not undergone HACS or Home Assistant integration validation. Install at your own risk, after a full backup.

1. In HACS, open the overflow menu (three dots), then **Custom repositories**.
2. Enter `https://github.com/B-Hartley/powerwall_export`, select **Integration** as the category, and add it.
3. Find **Powerwall Export** under HACS, select **Download**, then restart Home Assistant.
4. In **Settings > Devices & services > Add integration**, choose Powerwall Export and select your Tessie or Tesla Fleet authenticated energy-site connection.
5. Run **Check setup (read only)**. Inspect the Status sensor and do not proceed if it fails.

The Octopus blueprint YAML files in `blueprints/` do *not* install automatically via HACS custom-integration download. Copy them manually to matching locations under `/config/blueprints/` (or import a blueprint URL from your published repository). See README.md for step-by-step testing and operation.

## Manual installation

Copy `custom_components/powerwall_export` to `/config/custom_components/powerwall_export`, restart, and add the integration. The `brand/` folder belongs **inside** `custom_components/powerwall_export/`. Home Assistant may require separately registered brands to show an icon in all interfaces.

## Updating

Back up the configuration and recovery state. Update the integration with HACS and restart. Avoid updating while an export/prepared tariff is active; perform and verify restoration first. Do not delete `.storage/powerwall_export.site_*.json` while recovery is pending.

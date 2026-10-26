# Test report - Powerwall Export 0.1.0

Date: 11 September 2026

## Executed in the build environment

- `python -m pytest -q tests --disable-warnings`: **81 passed**.
- Parsed every delivered Python source with `ast.parse`; compilation also passed.
- All delivered JSON parsed successfully.
- All YAML parsed successfully, including the blueprint's `!input` references.
- Action/field translations match the three service schemas.
- ZIP file integrity verified after packaging.

## Coverage

Controller tests cover unchanged/default reserve, explicit reserve override,
original-mode restoration, conflicting starts, snapshot-before-write ordering,
disk-write failure, tariff-read failure, missing settings, grid/Storm Watch/VPP
preconditions, stale telemetry, start failures, timeout-after-success, retained
snapshots on failed restoration, retry recovery, reboot restoration, scheduled
expiry, reserve cutoff, live monitoring failures and read-back mismatch.

Tariff tests cover minute-level boundaries, midnight, year-end, leap day,
complete annual season coverage, buy/sell constraints, no next-week repetition,
rejected DST-transition windows, GET-to-POST normalisation and price-change
comparison. Recovery-file tests exercise atomic file replacement, private
permissions, failed rename, invalid JSON, unsupported versions and invalid
serialization. Adapter tests use minimal module stubs to check exact client
reuse, parent reload handling, tariff route/wrapping, command acknowledgements,
connection loss and sanitised error reporting.

## Not verified

**No live Home Assistant runtime, GUI/config flow, physical Powerwall, Tesla
cloud, Tessie account or Octopus account was available for an end-to-end test.**
The bridge tests use stubs; they are not a Home Assistant integration-test suite.
The source interfaces were checked against Core 2026.9.1 and the pinned
API-library version. Schema syntax checks are not a full HA blueprint validation.

Passing offline tests is not proof of hardware compatibility or guaranteed
export. Complete the read-only setup check and supervised five-minute
export/restore test described in README.md before enabling automation.

## v0.1.6 additional compatibility tests

94 tests passed offline, including synthetic replicas of linked five-day Octopus tariff seasons, manual Summer/Winter tariffs, overnight 23:30-05:30 periods, and a wrapping season. No live Powerwall write tested.

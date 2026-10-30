# Compatibility

This document tracks the supported NetBox and Python versions for each
release of Custom Objects.

| Plugin Version | Minimum NetBox Version | Maximum NetBox Version | Minimum Python |
|----------------|------------------------|------------------------|----------------|
| 0.7.1 | 4.5.2 | 4.7.x | 3.12 |
| 0.7.0 | 4.5.2 | 4.7.x | 3.12 |
| 0.6.1 | 4.5.2 | 4.7.x | 3.12 |
| 0.6.0 | 4.5.2 | 4.6.x | 3.12 |
| 0.5.3 | 4.5.2 | 4.6.x | 3.12 |
| 0.5.2 | 4.5.2 | 4.6.x | 3.12 |
| 0.5.1 | 4.5.2 | 4.6.x | 3.12 |
| 0.5.0 | 4.5.2 | 4.6.x | 3.12 |
| 0.4.10 | 4.4.0 | 4.5.x | 3.10 |
| 0.4.9 | 4.4.0 | 4.5.x | 3.10 |
| 0.4.8 | 4.4.0 | 4.5.x | 3.10 |
| 0.4.7 | 4.4.0 | 4.5.x | 3.10 |
| 0.4.6 | 4.4.0 | 4.5.x | 3.10 |
| 0.4.5 | 4.4.0 | 4.5.x | 3.10 |
| 0.4.4 | 4.4.0 | 4.5.x | 3.10 |
| 0.4.3 | 4.4.0 | 4.4.x | 3.10 |
| 0.4.2 | 4.4.0 | 4.4.x | 3.10 |
| 0.4.1 | 4.4.0 | 4.4.x | 3.10 |
| 0.4.0 | 4.4.0 | 4.4.x | 3.10 |
| 0.3.1 | 4.4.0 | 4.4.x | 3.10 |
| 0.3.0 | 4.4.0 | 4.4.x | 3.10 |

## Notes

| Note | Action |
|---|---|
| NetBox upgrade | Test against the target NetBox version before production rollout. |
| Upstream changes | Review the [NetBox release notes](https://docs.netbox.dev/en/stable/release-notes/). |
| Support range change | Add a matrix row and update `PluginConfig.min_version` / `max_version`. |

## Upgrading

See [Upgrading](https://netboxlabs.com/docs/extensions/custom-objects/upgrading/) in the documentation.

# Changelog

## 0.4.0
---

# New Features

**Limited Branching Compatibility for Custom Objects**

Custom Objects now has limited compatibility with NetBox Branching. Please see the documentation for more details.

# Enhancements

- [#37](https://github.com/netboxlabs/netbox-custom-objects/issues/37) - Limited Branching compatibility for Custom Objects
- [#42](https://github.com/netboxlabs/netbox-custom-objects/issues/42) - Populate default values for Multi-Object fields
- [#87](https://github.com/netboxlabs/netbox-custom-objects/issues/87) - Make Custom Object "name" clickable
- [#150](https://github.com/netboxlabs/netbox-custom-objects/issues/150) - Cannot import created CustomObject (Documentation)

# Bug Fixes

- [#70](https://github.com/netboxlabs/netbox-custom-objects/issues/70) - Incorrect validation message on integer field min/max defaults
- [#104](https://github.com/netboxlabs/netbox-custom-objects/issues/104) - Bulk edit not possible on fields of type `object`
- [#105](https://github.com/netboxlabs/netbox-custom-objects/issues/105) - Bulk edit not possible on fields of type `multiobject`
- [#172](https://github.com/netboxlabs/netbox-custom-objects/issues/172) - Postgres errors on startup
- [#189](https://github.com/netboxlabs/netbox-custom-objects/issues/189) - Exceptions when creating Custom Objects
- [#195](https://github.com/netboxlabs/netbox-custom-objects/issues/195) - RecursionError in netbox_custom_objects plugin due to circular dependencies in CustomObjectType Fields
- [#210](https://github.com/netboxlabs/netbox-custom-objects/issues/210) - Custom Objects plugin visible when logged out
- [#212](https://github.com/netboxlabs/netbox-custom-objects/issues/212) - Quick Search not working
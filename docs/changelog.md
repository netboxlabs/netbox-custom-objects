# Changelog

## 0.4.4
---

### Bug Fixes

- [#230](https://github.com/netboxlabs/netbox-custom-objects/issues/230) - Warning on unique object type fields
- [#284](https://github.com/netboxlabs/netbox-custom-objects/issues/284) - "Tags" field not listed in table config dialog
- [#294](https://github.com/netboxlabs/netbox-custom-objects/issues/294) - Creating journal entry on custom object item leaves Created By blank
- [#310](https://github.com/netboxlabs/netbox-custom-objects/issues/310) - Linkify primary field in custom object table view
- [#336](https://github.com/netboxlabs/netbox-custom-objects/issues/336) - Support NetBox v4.5 (Beta)


## 0.4.3
---

### Bug Fixes

- [#299](https://github.com/netboxlabs/netbox-custom-objects/issues/299) - Add additional checks for restricted names of custom object types
- [#326](https://github.com/netboxlabs/netbox-custom-objects/issues/326) - Improve initialization code check when running migrations
- [#330](https://github.com/netboxlabs/netbox-custom-objects/issues/330) - Fix limit checking for max_custom_object_types


## 0.4.2
---

### Enhancements

- [#278](https://github.com/netboxlabs/netbox-custom-objects/issues/278) - Add bulk import buttons to nav sidebar
- [#282](https://github.com/netboxlabs/netbox-custom-objects/issues/282) - Allow Admins to limit the number of Custom Object Types

### Bug Fixes

- [#266](https://github.com/netboxlabs/netbox-custom-objects/issues/266) - Creating / editing Custom Object via API with field type Multiple Object fails
- [#283](https://github.com/netboxlabs/netbox-custom-objects/issues/283) - IntegrityError when deleting a CO that is referenced by another CO
- [#287](https://github.com/netboxlabs/netbox-custom-objects/issues/287) - Description field missing from API views
- [#290](https://github.com/netboxlabs/netbox-custom-objects/issues/290) - Improve API object_types Labels for Custom Object Types
- [#313](https://github.com/netboxlabs/netbox-custom-objects/issues/313) - Cap number of "Custom Objects linking to this object" on Detail view


## 0.4.1
---

### Bug Fixes

- [#237](https://github.com/netboxlabs/netbox-custom-objects/issues/237) - Incorrect validation error when adding multiple fields pointing to the same Custom Object Type
- [#251](https://github.com/netboxlabs/netbox-custom-objects/issues/251) - Bulk import broken due to incorrect slug handling
- [#273](https://github.com/netboxlabs/netbox-custom-objects/issues/273) - `group_name` missing from Custom Object Type serializer


## 0.4.0
---

### New Features

**Limited Branching Compatibility for Custom Objects**

Custom Objects now has limited compatibility with NetBox Branching. Please see the documentation for more details.

### Enhancements

- [#37](https://github.com/netboxlabs/netbox-custom-objects/issues/37) - Limited Branching compatibility for Custom Objects
- [#42](https://github.com/netboxlabs/netbox-custom-objects/issues/42) - Populate default values for Multi-Object fields
- [#87](https://github.com/netboxlabs/netbox-custom-objects/issues/87) - Make Custom Object "name" clickable
- [#150](https://github.com/netboxlabs/netbox-custom-objects/issues/150) - Cannot import created CustomObject (Documentation)

### Bug Fixes

- [#70](https://github.com/netboxlabs/netbox-custom-objects/issues/70) - Incorrect validation message on integer field min/max defaults
- [#104](https://github.com/netboxlabs/netbox-custom-objects/issues/104) - Bulk edit not possible on fields of type `object`
- [#105](https://github.com/netboxlabs/netbox-custom-objects/issues/105) - Bulk edit not possible on fields of type `multiobject`
- [#172](https://github.com/netboxlabs/netbox-custom-objects/issues/172) - Postgres errors on startup
- [#189](https://github.com/netboxlabs/netbox-custom-objects/issues/189) - Exceptions when creating Custom Objects
- [#195](https://github.com/netboxlabs/netbox-custom-objects/issues/195) - RecursionError in netbox_custom_objects plugin due to circular dependencies in CustomObjectType Fields
- [#210](https://github.com/netboxlabs/netbox-custom-objects/issues/210) - Custom Objects plugin visible when logged out
- [#212](https://github.com/netboxlabs/netbox-custom-objects/issues/212) - Quick Search not working
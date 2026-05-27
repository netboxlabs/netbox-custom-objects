# Releases

## 0.5.1

### Bug Fixes

- [#380](https://github.com/netboxlabs/netbox-custom-objects/issues/380) - Bulk edit "Select all N objects matching query" only selected the current page
- [#396](https://github.com/netboxlabs/netbox-custom-objects/issues/396) - Add permission was not sufficient to access the add URL; change permission was incorrectly required
- [#408](https://github.com/netboxlabs/netbox-custom-objects/issues/408) - Cross-COT FK fields missing after server restart
- [#443](https://github.com/netboxlabs/netbox-custom-objects/issues/443) - API updates failed for some objects due to non-dict data in generated serializer `validate()`
- [#477](https://github.com/netboxlabs/netbox-custom-objects/issues/477) - Deleting a custom object via the UI raised a `ValueError` due to through-table entries being included in the delete confirmation queryset
- [#483](https://github.com/netboxlabs/netbox-custom-objects/issues/483) - Deletion of objects with M2M relations failed due to stale `path_infos` on through-model FKs after COT model regeneration
- [#500](https://github.com/netboxlabs/netbox-custom-objects/issues/500) - Viewing the changelog for a custom object raised `unexpected keyword argument 'user'`
- [#503](https://github.com/netboxlabs/netbox-custom-objects/issues/503) - FilterSet `ValueError` caused by `ModelChoiceFilter` not handling polymorphic objects; replaced with `NonPolymorphicObjectFilter`
- [#507](https://github.com/netboxlabs/netbox-custom-objects/issues/507) - Migration 0011 failed with `DuplicateObject` on partial re-run and raised errors for long table names
- [#508](https://github.com/netboxlabs/netbox-custom-objects/issues/508) - `ValueError: Must be 'TableNModel' instance` in `CustomObjectLink.left_page()` due to `no_cache=True` breaking dynamic model identity
- [#511](https://github.com/netboxlabs/netbox-custom-objects/issues/511) - Raised minimum compatible NetBox version to 4.5.2
- [#517](https://github.com/netboxlabs/netbox-custom-objects/issues/517) - Mixed-case field names created quoted PostgreSQL identifiers that broke schema cloning; migration 0014 renames them to lowercase
- [#519](https://github.com/netboxlabs/netbox-custom-objects/issues/519) - Migration 0011 raised `constraint already exists` when `table_schema` was not filtered to the current schema

---

## 0.5.0

### New Features

**Polymorphic Object Fields**

Object and multiobject fields can now reference objects of multiple content types via a generic foreign key. Enabled per-field with the `is_polymorphic` flag; allowed types are configured via the `related_object_types` M2M relation.

- [#31](https://github.com/netboxlabs/netbox-custom-objects/issues/31) - Allow GenericForeignKey Custom Object Type Fields

**Portable Schema System**

Custom Object Type definitions can now be exported, compared, and applied as portable JSON schema documents, enabling version-controlled schema management and automated COT lifecycle operations.

- [#386](https://github.com/netboxlabs/netbox-custom-objects/issues/386) - Define a schema format for portable Custom Object Type definitions
- [#387](https://github.com/netboxlabs/netbox-custom-objects/issues/387) - Custom Object Type state comparator
- [#388](https://github.com/netboxlabs/netbox-custom-objects/issues/388) - Custom Object Type schema exporter
- [#389](https://github.com/netboxlabs/netbox-custom-objects/issues/389) - Custom Object Type schema executor (upgrade tool)
- [#390](https://github.com/netboxlabs/netbox-custom-objects/issues/390) - Schema validation and apply API endpoints

### Enhancements

- [#49](https://github.com/netboxlabs/netbox-custom-objects/issues/49) - Support NetBox `CUSTOM_VALIDATORS` setting keyed by COT slug (e.g. `netbox_custom_objects.my-slug`)
- [#224](https://github.com/netboxlabs/netbox-custom-objects/issues/224) - Accept `app_label`/`model` in API when creating object-type fields (removes requirement for `related_object_type` ID)
- [#270](https://github.com/netboxlabs/netbox-custom-objects/issues/270) - Add context field on Custom Object Type Fields to support secondary contextual info in dropdown selects
- [#296](https://github.com/netboxlabs/netbox-custom-objects/issues/296) / [#366](https://github.com/netboxlabs/netbox-custom-objects/issues/366) - Filterset and filter-form support for all custom field types (object, multiobject, boolean, select)
- [#385](https://github.com/netboxlabs/netbox-custom-objects/issues/385) - Add `related_name` field to Custom Object Type Fields for configurable reverse accessor names
- [#391](https://github.com/netboxlabs/netbox-custom-objects/issues/391) - Automatically heal mixin column drift on `post_migrate` to keep COT schemas consistent with base class changes
- [#392](https://github.com/netboxlabs/netbox-custom-objects/issues/392) - Validate Custom Object Type `version` field as a PEP 440 semantic version string
- [#397](https://github.com/netboxlabs/netbox-custom-objects/issues/397) - Add branch limitation warnings to all write-operation views

### Bug Fixes

- [#488](https://github.com/netboxlabs/netbox-custom-objects/issues/488) - Make custom-object FK constraints non-DEFERRABLE to prevent potential deadlocks

---

## 0.4.10

### Bug Fixes

- [#456](https://github.com/netboxlabs/netbox-custom-objects/issues/456) - Additional guards against a partially-migrated schema crashing during `manage.py migrate`

---

## 0.4.9

### Bug Fixes

- [#456](https://github.com/netboxlabs/netbox-custom-objects/issues/456) - Error executing migration due to missing `group_name` column when upgrading from v0.4.6

---

## 0.4.8

**Note:** See also v0.4.7 for recent bug fixes and enhancements, as this release is a fast-follow.

### Bug Fixes

- [#441](https://github.com/netboxlabs/netbox-custom-objects/issues/441) - ObjectSelectorView does not support targeting custom objects from core custom fields

---

## 0.4.7

### Enhancements

- [#25](https://github.com/netboxlabs/netbox-custom-objects/issues/25) - Linked custom objects should show up in the API response for related objects
- [#193](https://github.com/netboxlabs/netbox-custom-objects/issues/193) - Grouping custom object types in nav menu
- [#292](https://github.com/netboxlabs/netbox-custom-objects/issues/292) - Move COTF to their own standard ViewTab in the COT detail view
- [#308](https://github.com/netboxlabs/netbox-custom-objects/issues/308) - Limit rows qty in "Custom Objects linking to this object" panels

### Bug Fixes

- [#382](https://github.com/netboxlabs/netbox-custom-objects/issues/382) - Primary name field breaks related custom objects and NetBox objects
- [#383](https://github.com/netboxlabs/netbox-custom-objects/issues/383) - Related objects and count on NetBox Objects are rendered twice
- [#394](https://github.com/netboxlabs/netbox-custom-objects/issues/394) - Reindex CachedValues when COT fields are changed
- [#407](https://github.com/netboxlabs/netbox-custom-objects/issues/407) - Custom object types visible in menu without permissions
- [#409](https://github.com/netboxlabs/netbox-custom-objects/issues/409) - Required Fields also required when bulk editing
- [#417](https://github.com/netboxlabs/netbox-custom-objects/issues/417) - Filtering objects by multiple-object field does not work
- [#423](https://github.com/netboxlabs/netbox-custom-objects/issues/423) - Can't use custom object as field type in POST /api/plugins/custom-objects/custom-object-type-fields/
- [#429](https://github.com/netboxlabs/netbox-custom-objects/issues/429) - Deleting Custom Object and Custom Object Type Together Causes Missing Relation Error
- [#440](https://github.com/netboxlabs/netbox-custom-objects/issues/440) - Typeahead search returns no results for non-text primary fields

---

## 0.4.6

### Bug Fixes

- [#348](https://github.com/netboxlabs/netbox-custom-objects/issues/348) - Saving a custom object type field breaks object-field relationships
- [#372](https://github.com/netboxlabs/netbox-custom-objects/issues/372) - Double queryset evaluation in custom object list view

---

## 0.4.5

### Bug Fixes

- [#264](https://github.com/netboxlabs/netbox-custom-objects/issues/264) - Make fields in bulk-edit not required
- [#317](https://github.com/netboxlabs/netbox-custom-objects/issues/317) - Add missing serializer Fields to CustomObjectTypeSerializer
- [#340](https://github.com/netboxlabs/netbox-custom-objects/issues/340) - Improve query performance for related models
- [#351](https://github.com/netboxlabs/netbox-custom-objects/issues/351) - Prevent makemigrations from picking up custom object changes

---

## 0.4.4

### Bug Fixes

- [#230](https://github.com/netboxlabs/netbox-custom-objects/issues/230) - Warning on unique object type fields
- [#284](https://github.com/netboxlabs/netbox-custom-objects/issues/284) - "Tags" field not listed in table config dialog
- [#294](https://github.com/netboxlabs/netbox-custom-objects/issues/294) - Creating journal entry on custom object item leaves Created By blank
- [#310](https://github.com/netboxlabs/netbox-custom-objects/issues/310) - Linkify primary field in custom object table view
- [#336](https://github.com/netboxlabs/netbox-custom-objects/issues/336) - Support NetBox v4.5 (Beta)

---

## 0.4.3

### Bug Fixes

- [#299](https://github.com/netboxlabs/netbox-custom-objects/issues/299) - Add additional checks for restricted names of custom object types
- [#326](https://github.com/netboxlabs/netbox-custom-objects/issues/326) - Improve initialization code check when running migrations
- [#330](https://github.com/netboxlabs/netbox-custom-objects/issues/330) - Fix limit checking for max_custom_object_types

---

## 0.4.2

### Enhancements

- [#278](https://github.com/netboxlabs/netbox-custom-objects/issues/278) - Add bulk import buttons to nav sidebar
- [#282](https://github.com/netboxlabs/netbox-custom-objects/issues/282) - Allow Admins to limit the number of Custom Object Types

### Bug Fixes

- [#266](https://github.com/netboxlabs/netbox-custom-objects/issues/266) - Creating / editing Custom Object via API with field type Multiple Object fails
- [#283](https://github.com/netboxlabs/netbox-custom-objects/issues/283) - IntegrityError when deleting a CO that is referenced by another CO
- [#287](https://github.com/netboxlabs/netbox-custom-objects/issues/287) - Description field missing from API views
- [#290](https://github.com/netboxlabs/netbox-custom-objects/issues/290) - Improve API object_types Labels for Custom Object Types
- [#313](https://github.com/netboxlabs/netbox-custom-objects/issues/313) - Cap number of "Custom Objects linking to this object" on Detail view

---

## 0.4.1

### Bug Fixes

- [#237](https://github.com/netboxlabs/netbox-custom-objects/issues/237) - Incorrect validation error when adding multiple fields pointing to the same Custom Object Type
- [#251](https://github.com/netboxlabs/netbox-custom-objects/issues/251) - Bulk import broken due to incorrect slug handling
- [#273](https://github.com/netboxlabs/netbox-custom-objects/issues/273) - `group_name` missing from Custom Object Type serializer

---

## 0.4.0

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

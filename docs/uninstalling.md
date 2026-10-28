# Uninstalling

NetBox's guide to [removing a plugin](https://netboxlabs.com/docs/netbox/plugins/removal/) covers most of the process. Custom Objects needs one extra step first: deleting its Custom Object Types, each of which has its own database tables that the generic guide doesn't find.

!!! warning
    Uninstalling deletes every Custom Object Type and all of their objects. Back up the NetBox database before you start. To keep a copy of your type definitions, [export them](portable-schema.md) first.

## 1. Remove Branch Copies

If you use [NetBox Branching](branching.md), merge any changes you want to keep, then archive or delete every branch that contains Custom Objects tables. This includes branches with no custom-object changes and branches that have already been merged: merging preserves their database tables.

## 2. Delete Every Custom Object Type

While the plugin is still installed, delete all Custom Object Types, either in the UI (**Custom Objects > Custom Object Types**, select all, then **Delete Selected**) or through the [REST API](rest-api.md). Deleting a type drops its database tables and deletes all of its objects. It also deletes:

- any NetBox custom field whose related object type is that Custom Object Type, and
- any field of another Custom Object Type that references it. A polymorphic field instead stops allowing it, and is deleted only if it allowed no other types.

## 3. Remove the Plugin

Follow NetBox's [plugin removal guide](https://netboxlabs.com/docs/netbox/plugins/removal/), using:

| | |
|---|---|
| Package | `netboxlabs-netbox-custom-objects` |
| `PLUGINS` entry and app label | `netbox_custom_objects` |
| Database tables | `netbox_custom_objects_*` |

After deleting the types and removing their branch copies, run `\dt *.custom_objects_*` in the database shell to check all schemas. No matching tables should remain.

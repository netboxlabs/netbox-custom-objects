# Permissions

Custom Objects uses NetBox's standard [permissions](https://netboxlabs.com/docs/netbox/administration/permissions/). Users are granted access with object permissions, which can be limited to particular actions (view, add, change, delete) and, with constraints, to particular objects.

There are two separate things to grant access to: the definitions of Custom Object Types, and the objects of each type.

## Custom Object Type Definitions

Three object types in the **Custom Objects** group cover the definitions:

| Object type | Controls |
|-------------|----------|
| Custom Objects > Custom Object Type | The types themselves. |
| Custom Objects > Custom Object Type Field | Each type's fields. |
| Custom Objects > Custom Object Type Constraint | Each type's constraints. |

Adding a field or constraint from a type's page also needs change permission on the type, which shows the page's **Add** menu. [Applying a portable schema document](portable-schema.md#applying-a-schema-api) needs add and change permission on Custom Object Types, plus permission for each change the document makes.

!!! warning
    Deleting a Custom Object Type drops its database table and every object of that type, and deleting a field drops its column. We recommend that only administrators may delete Custom Object Types and fields.

## Custom Objects

Each Custom Object Type is a separate object type for permissions, listed under its display name in the **Custom Objects** group, e.g. **Custom Objects > DHCP Scope**. Grant view, add, change and delete permission on it as for any NetBox model, with constraints on its fields if needed. For example, a constraint of `{"environment": "production"}` limits a permission to objects whose `environment` field is `production`.

Internally, each type's permissions are named after the model that backs the type, e.g. `netbox_custom_objects.view_table9model` for the type with ID 9. These are the names to use in code, such as `user.has_perm()`.

Permission on a type's objects also determines what users see elsewhere:

- **Navigation.** A type appears in the navigation menu for users who can view its objects, with **Add** and **Import** buttons for users who can add them.
- **Type definitions.** Users who can view a type's objects can also see the type's structure, so they can work with the objects without permission on Custom Object Types: the type is listed at the root of the [REST API](rest-api.md), and appears in the [GraphQL](graphql.md) schema.
- **Custom Objects tab.** The [Custom Objects tab](related-objects-tab.md) on NetBox objects only lists custom objects the user can view.

## Suggested Roles

| Role | Permissions |
|------|-------------|
| Schema administrator | All actions on Custom Object Type, Custom Object Type Field and Custom Object Type Constraint. |
| Editor | View, add, change and delete on the Custom Object Types they work with, e.g. **Custom Objects > DHCP Scope**. |
| Read-only user | View on those Custom Object Types. |

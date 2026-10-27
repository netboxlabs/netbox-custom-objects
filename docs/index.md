# NetBox Custom Objects

[NetBox](https://github.com/netbox-community/netbox) is the world's leading source of truth for infrastructure, featuring an extensive data model. Sometimes it can be useful to extend the NetBox data model to fit specific organizational needs. The Custom Objects plugin lets NetBox administrators extend the NetBox data model without writing a line of code.

This documentation covers:

- Setting up: [installation](installation.md), [configuration](configuration.md), [permissions](permissions.md), [upgrading](upgrading.md) and [uninstalling](uninstalling.md)
- [Field attributes](field-attributes.md) available on each field type
- Working with custom objects: [import and export](import-export.md), the [Custom Objects tab](related-objects-tab.md), and [config context and templates](config-context.md)
- [REST API](rest-api.md) usage and endpoints
- [GraphQL API](graphql.md) queries and filters
- [Using Custom Objects with NetBox Branching](branching.md)
- [Portable schema](portable-schema.md) — exporting and applying schemas across environments
- [Troubleshooting](troubleshooting.md)

!!! tip
    If you hit any problems please check the [existing issues](https://github.com/netboxlabs/netbox-custom-objects/issues) before creating a new one. If you're unsure, start a [discussion](https://github.com/netboxlabs/netbox-custom-objects/discussions).

!!! tip
    If you are using Custom Objects with Custom Scripts or Plugins you will need to use the following method to import and instantiate a Custom Object as a class that will behave like a standard Django model:

    ```python
    from netbox_custom_objects.models import CustomObjectType

    custom_object_type = CustomObjectType.objects.get(name="cat")
    Cat = custom_object_type.get_model_with_serializer()
    ```

    `get_model_with_serializer()` also registers the model's REST API serializer, which NetBox needs to record changes when objects are saved. On NetBox 4.6.2 and later, `get_model()` is enough.

    ```python
    In [21]: Cat.objects.count()
    Out[21]: 3
    ```

## Features

* Easily create new object types in NetBox — via the GUI, the REST API, or `pynetbox`

* Each Custom Object Type inherits standard NetBox model functionality including:
    * List views and detail views
    * Grouped fields
    * An entry in the left navigation pane
    * NetBox Custom Fields pointing to Custom Object Types
    * REST API endpoints
    * Full-text search
    * Change logging
    * Bookmarks
    * Contacts
    * Custom Links
    * Custom Validation (`CUSTOM_VALIDATORS`)
    * Cloning
    * Import/Export, including export templates
    * Event Rules
    * Notifications
    * Journaling
    * Owners
    * Tags

* A [**Custom Objects** tab](related-objects-tab.md) on NetBox objects lists the custom objects that reference them.

* Optional [config context](config-context.md) support, and access to custom objects from Jinja config and export templates.

* Custom Object Types can include fields of all standard types — text, decimal, integer, boolean, and more — as well as references to choice sets, core NetBox models, plugin models, and other Custom Object Types. See [Field Attributes](field-attributes.md) for the complete list of field types and per-type options.

* Object reference fields support both single-type references (`object`, `multiobject`) and polymorphic references that may point to objects of multiple different types.

* Custom Object Type Fields can model additional behavior such as uniqueness enforcement, default values, layout hints, required fields, and more.

* Custom Object Type definitions can be exported as portable JSON schema documents, versioned in source control, and applied across environments — see [Portable Schema](portable-schema.md).

## Terminology

* A **Custom Object Type** is a new object in NetBox. For example, you may decide to add a Custom Object Type to model a `DHCP Scope`. In NetBox Plugin terminology, this is equivalent to a model.

* A **Custom Object Type Field** is a field on a given Custom Object Type. For example, you may add a `range` field to your `DHCP Scope` Custom Object Type.

* A **Custom Object** is an instance of a Custom Object Type. For example, having created your `DHCP Scope` Custom Object Type, you can now create individual DHCP Scope objects. Each DHCP Scope you create is a Custom Object.


## Workflow

Let's walk through the DHCP Scope example to highlight the steps involved in creating a Custom Object Type and then interacting with instances of it.

### Create the Custom Object Type

1. Navigate to the Custom Objects plugin in the left navigation pane and click the `+` next to `Custom Object Types`.
2. Choose the relevant naming for your Custom Object Type.

| Field                   | Value         |
|-------------------------|---------------|
| Internal name           | `dhcp_scope`  |
| Display name (singular) | `DHCP Scope`  |
| Display name (plural)   | `DHCP Scopes` |
| URL path/slug           | `dhcp-scopes` |

Additional optional fields include `Description`, `Version`, `Display expression`, `Group name`, `Config context support`, `Tags` and `Comments`. A display expression is a Jinja template, such as `Scope {{ range }}`, that sets how each object is named. The `Group name` field controls how Custom Object Types are grouped together in the left navigation menu.

!!! tip
    The `Version` field accepts a [PEP 440](https://peps.python.org/pep-0440/) version string (e.g. `1.0.0`). This becomes useful when managing schemas across environments using the [portable schema](portable-schema.md) feature.

3. Click `Create`.

### Adding Fields to the Custom Object Type

1. After creating your Custom Object Type you will be taken to the Custom Object Type detail view. To add a field, click `Add` and choose `Field`.

!!! tip
    The `Primary name field` flag on a Custom Object Type Field controls how Custom Objects are named in the UI. By default, a Custom Object is named `<display name> <ID>` — so in this example, the first DHCP Scope created would be named `DHCP Scope 1`. Enabling `Primary name field` on a field causes the value of that field to be used as the object's display name instead. A type's display expression, if set, takes precedence over both.

!!! tip
    The `Context field` flag causes a field's value to be shown as additional context when this object is referenced by another object.

!!! tip
    Uniqueness cannot be enforced for Custom Object Type Fields of type `multiobject`, `boolean` or `coordinates`.

2. Specify a `Name` for your field, in this case we'll choose a URL-friendly value: `range`.
3. Specify the `Label` for your field. This is a human-readable name that will be used in the GUI. In this case we'll choose `DHCP Range`.
4. Choose a `Type` for your field. In this case we want our `range` field to reference a single built-in NetBox object, so we choose `Object`.
5. Then we need to specify which type of built-in object our `range` field will reference. Scroll down to `Related object type` and choose `IPAM > IP Range`.
6. Then click `Create`.

See [Field Attributes](field-attributes.md) for the full list of attributes available on each field type.

### Adding Constraints to the Custom Object Type

A field's `Must be unique` setting makes its values unique on their own. To require that a *combination* of field values is unique, for example a serial number that only has to be unique per vendor, add a constraint: open the Custom Object Type, click `Add` and choose `Constraint`.

- **Fields** — the fields whose combined values must be unique. Pick at least two, or one when the constraint is case-insensitive or has a condition. Text, long text, integer, decimal, boolean, date, datetime, URL, selection, color, MAC address and object fields (including polymorphic object fields, which count as both the object's type and its ID) can be included.
- **Case-insensitive** — compare text and URL fields without regard to case, so `ACME` and `acme` conflict. The constraint must include at least one text or URL field.
- **Empty values are distinct** — enabled by default, so objects that leave any of the constrained fields empty never conflict. Disable it to treat empty values as equal; this requires PostgreSQL 15 or later.
- **Condition** — optionally, apply the constraint only to objects that match all of a list of predicates, for example to make `name` unique only among objects with no `parent`:

    ```json
    [{"field": "parent", "lookup": "isnull", "value": true}]
    ```

    Each predicate names a field and a lookup: `exact`, `n` (does not equal, or is empty), `in` (a list of values), `isnull` (`true` for empty, `false` for not empty), or `gt`, `gte`, `lt` and `lte` (greater than, at least, less than, at most; integer, decimal, date and datetime fields only). Instead of a value, `{"field": "other_field"}` compares with another field of the same kind (text, URL and selection; integer and decimal; date; datetime; boolean; color; or MAC address). The same field types as above can be tested; object fields can only be tested with `isnull`. Blank text counts as empty, so test for it with `isnull` rather than comparing with `""`; in a condition, a comparison with an empty value doesn't match.

To require something of every object instead, such as an end date after its start date, add a constraint of type **Check**:

- **Rule** — predicates, in the same form as a condition, that every object must satisfy:

    ```json
    [{"field": "end_date", "lookup": "gt", "value": {"field": "start_date"}}]
    ```

    A comparison passes when a value it compares is empty, so this rule allows objects with no start or end date. To require a value, add a predicate such as `{"field": "end_date", "lookup": "isnull", "value": false}`.
- **Condition** — optionally, apply the rule only to objects that match it. For example, to require a bandwidth above zero for WAN links only, use the condition `[{"field": "kind", "lookup": "exact", "value": "wan"}]` with the rule `[{"field": "bandwidth_mbps", "lookup": "gt", "value": 0}]`.
- **Error message** — optionally, the error to show when an object breaks the rule, e.g. "The end date must be after the start date." Without one, the error describes the rule.

A check constraint has no fields of its own, isn't case-insensitive, and keeps empty values distinct; those settings apply to unique constraints.

The database enforces constraints, so they apply to the UI, the REST API, imports and scripts alike, and a constraint can't be added while existing objects already break it. Saving an object that would break one shows a validation error.

A constrained field can be renamed. Its type can't be changed until it's removed from its constraints (including their conditions and rules), and deleting it deletes the constraints that include or test it.

### Interacting with Custom Objects

Typically, NetBox administrators are responsible for thinking through modelling requirements and creating Custom Object Types for other users to interact with day-to-day. Having created a `DHCP Scope` Custom Object Type, here is how others interact with it.

#### Creating a New Custom Object

1. Under the NetBox Custom Objects plugin in the left navigation pane you will now see `DHCP Scopes`. Click the `+` next to your new Custom Object Type.
2. As you added a single field labelled `DHCP Range` that references an IP Range, you are prompted to specify a range. Select one and click `Create`.
3. You are taken to the detail view for your new `DHCP Scope` object.

#### Standard List Views for Custom Objects

As with core NetBox objects, Custom Objects have their own list views. To see all your `DHCP Scopes`, click on your Custom Object Type in the left navigation pane under the Custom Objects plugin section.

You will see a standard NetBox list view for your new Custom Objects with the standard options including `Configure Table`, `+ Add`, `Import`, `Export`, and others. See [Import and Export](import-export.md) for the import format.

### Deletions

#### Deleting Custom Object Types

Deleting a Custom Object Type drops an entire database table and should be done with caution. You will be warned about the impact before you proceed. We recommend that only administrators have permission to delete Custom Object Types; see [Permissions](permissions.md).

#### Deleting Custom Object Type Fields

Deleting a Custom Object Type Field drops an entire database column and should be done with caution. You will be warned about the impact before you proceed, including any constraints that will be deleted with the field. We recommend that only administrators have permission to delete Custom Object Type Fields.

#### Deleting Custom Objects

Deleting Custom Objects works just like deleting any other NetBox object. You can delete a Custom Object from its detail view or from the list view. Follow your usual permissions practices.

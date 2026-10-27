# Import and Export

Custom objects can be imported and exported in bulk like NetBox's own objects, from the **Import** and **Export** buttons on a type's list view.

## Bulk Import

The **Import** button (shown to users who can add the type's objects) accepts CSV, JSON or YAML. Each column, or key, is a field's name, and each row creates an object. As in NetBox, include an `id` column to update existing objects instead of creating new ones.

Values are given as follows:

| Field type | Value |
|------------|-------|
| text, long text, integer, decimal, date, datetime, color, MAC address | The value, as typed in the edit form. |
| boolean | `true` or `false`. |
| JSON | A JSON value. |
| URL | The URL. Its link title goes in a separate `<field>_title` column. |
| coordinates | Two columns, `<field>_latitude` and `<field>_longitude`. |
| array | The list as JSON, e.g. `"[1, 2, 3]"`. |
| select | The choice's value. |
| multi-select | The choices' values, comma-separated, e.g. `"red,blue"`. |
| object | The referenced object, identified as described below. |
| multi-object | The referenced objects, comma-separated. |
| tags | Tag slugs, comma-separated. |

An object or multi-object field identifies the objects it references by a natural identifier, such as a site's or device's name. The plugin uses the first of `name`, `slug`, `model` and `identifier` that the referenced object type has, preferring one that's unique. If it has none of them, as with most custom objects, use the object's ID.

!!! note
    - Polymorphic object and multi-object fields can't be imported, and are left out of the import form.
    - Fields whose **UI editable** setting isn't **Yes** are left out too, as in NetBox.

Imports are validated like objects created in the edit form, so required fields, validation rules, uniqueness and [constraints](index.md#adding-constraints-to-the-custom-object-type) all apply.

## Export

The **Export** button on a type's list view exports the current view (its visible columns) or all data as CSV. Any [export template](https://netboxlabs.com/docs/netbox/models/extras/exporttemplate/) assigned to the Custom Object Type is listed there too, and can also be used [through the REST API](rest-api.md#export-templates). Export templates can also read other types' objects through the [`custom_objects` template variable](config-context.md#jinja-config-and-export-templates).

To copy type *definitions*, rather than objects, between NetBox instances, use [portable schema](portable-schema.md) documents.

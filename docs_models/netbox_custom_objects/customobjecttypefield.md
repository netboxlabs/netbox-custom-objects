# Custom Object Type Fields

A Custom Object Type Field adds a field to a Custom Object Type: a column in the type's database table, an input on its edit form, and an attribute in its REST API and GraphQL representations. See the [field attributes documentation](https://github.com/netboxlabs/netbox-custom-objects/blob/main/docs/field-attributes.md) for each field type and the attributes specific to it.

## Fields

### Custom Object Type

The Custom Object Type this field belongs to.

### Name

The internal field name, unique within the Custom Object Type, e.g. `vendor_label`. Only lowercase alphanumeric characters and underscores are permitted.

### Label

The name of the field as displayed to users. If left blank, the field's name is used.

### Primary Name Field

Use this field's value as each object's displayed name. A Custom Object Type has at most one primary name field, so marking a field as primary unmarks any other.

### Context Field

Show this field's value as context when the object is referenced by other objects.

### Group Name

Fields with the same group name are displayed together.

### Description

A short, optional description, shown as help text below the field in forms.

### Type

The type of data the field holds, e.g. text, integer, select, object or array. Some types can't be changed to others once the field exists.

### Required

A value must be provided when an object is created or edited.

### Must Be Unique

No two objects of the type may share a value for this field. To make a combination of fields unique, add a [constraint](customobjecttypeconstraint.md) instead.

### Default

The value pre-populated when a new object is created. It must be a JSON value; enclose strings in double quotes, e.g. `"Foo"`.

### Polymorphic

For object and multi-object fields: allow references to objects of more than one type. It can't be changed after the field is created.

### Related Object Type

For non-polymorphic object and multi-object fields: the type of object the field references, either a NetBox object type or another Custom Object Type.

### Related Object Types

For polymorphic fields: the types of object the field may reference.

### Related Object Filter

For object and multi-object fields: a JSON dictionary of query parameters that filters the choices offered in the object selector, e.g. `{"status": "active"}`.

### Reverse Relation Name

For object and multi-object fields: the name of the accessor on the related object that leads back to this type's objects, e.g. `ssl_profiles`. If left blank, a unique name is generated.

### On Delete Behavior

For object fields: what happens to this object when the object it references is deleted. `Set null` clears the field, `Cascade` deletes this object too, and `Protect` prevents the deletion.

### Choice Set

For select and multi-select fields: the choice set that defines the available options.

### Minimum Value and Maximum Value

For integer and decimal fields, and arrays of them: the range every value must fall in.

### Validation Regex

For text and long text fields, and arrays of text: a regular expression every value must match, e.g. `^[A-Z]{3}$`.

### Element Type, Tuple Size, Minimum Length and Maximum Length

For array fields: the type of each value, the size of each tuple (if the array holds tuples), and the number of items allowed. The element type and tuple size can't be changed after the field is created.

### Search Weight

The field's weight in global search. Lower values are more important, and a weight of `0` leaves the field out of search.

### Filter Logic

How filters match the field's value: `Loose` matches any part of it, `Exact` matches the whole value, and `Disabled` turns off filtering.

### UI Visible

Whether the field is shown on the object's detail page: always, only when it has a value, or never.

### UI Editable

Whether the field can be edited in the UI: yes, no (shown read-only), or hidden.

### Display Weight

The field's position in forms and on the detail page. Fields with higher weights appear lower.

### Is Cloneable

Copy this field's value when an object is cloned.

### Comments

Free-form notes about the field.

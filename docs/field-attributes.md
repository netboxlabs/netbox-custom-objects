# Custom Object Field Attributes

The following attributes are available when creating or editing a Custom Object Type Field.

## Available Field Types

| Type | Description |
|------|-------------|
| `text` | Short text |
| `longtext` | Long text (rendered as a textarea) |
| `integer` | Integer number |
| `decimal` | Decimal number |
| `boolean` | True/false |
| `date` | Date |
| `datetime` | Date and time |
| `url` | URL |
| `json` | Arbitrary JSON value |
| `select` | Single selection from a choice set |
| `multiselect` | Multiple selections from a choice set |
| `object` | Reference to a single object (built-in NetBox object or Custom Object) |
| `multiobject` | Reference to multiple objects of the same type |

## Common Attributes

| Attribute | Description |
|-----------|-------------|
| `Name` | Internal field name. Must be lowercase alphanumeric with underscores only (e.g. `rack_unit`). |
| `Label` | Human-readable display name shown in the UI. Defaults to the field name. |
| `Type` | Data type of the field (see above). |
| `Description` | Help text shown below the field in forms. |
| `Group name` | Fields sharing the same group name are displayed together. |
| `Required` | When enabled, a value must be provided when creating or editing an object. |
| `Must be unique` | When enabled, no two objects of this type may share the same value for this field. Not supported for `boolean` or `multiobject` fields. |
| `Primary name field` | When enabled, this field's value is used as the object's display name. |
| `Context field` | When enabled, this field's value is shown as context when this object is referenced by another object. |
| `Default` | Default value pre-populated when creating a new object. Must be a valid JSON value. |
| `Display weight` | Controls the field's position in forms and detail views; higher weights appear lower. Default: `100`. |
| `Search weight` | Relevance weight for full-text search. Lower values are more important; `0` disables search indexing for this field. Default: `500`. |
| `Filter logic` | `Loose` (match any substring), `Exact` (match whole value), or `Disabled`. Default: `Loose`. |
| `UI visible` | Controls visibility in detail views: `Always`, `If set`, or `Hidden`. Default: `Always`. |
| `UI editable` | Controls editability in forms: `Yes`, `No` (read-only), or `Hidden`. Default: `Yes`. |
| `Is cloneable` | When enabled, this field's value is copied when cloning an object. |
| `Comments` | Free-form notes about this field (supports Markdown). |
| `Deprecated` | Marks the field as read-only; new values cannot be entered. Use during a migration grace period. |
| `Deprecated since` | [PEP 440](https://peps.python.org/pep-0440/) version string indicating the schema version in which the field was deprecated (e.g. `2.0.0`). |
| `Scheduled removal` | [PEP 440](https://peps.python.org/pep-0440/) version string indicating the schema version in which the field is planned to be removed (e.g. `3.0.0`). |

## Text Fields

Field types: `text`, `longtext`

| Attribute | Description |
|-----------|-------------|
| `Validation regex` | Regular expression enforced on field values. For example, `^[A-Z]{3}$` limits values to exactly three uppercase letters. |

## Numeric Fields

Field types: `integer`, `decimal`

| Attribute | Description |
|-----------|-------------|
| `Minimum value` | Minimum allowed numeric value. |
| `Maximum value` | Maximum allowed numeric value. |

## Choice Fields

Field types: `select`, `multiselect`

| Attribute | Description |
|-----------|-------------|
| `Choice set` | A NetBox [Custom Field Choice Set](https://netboxlabs.com/docs/netbox/customization/custom-fields/#custom-field-choices) that defines the available options. Required. |

## Object Reference Fields

Field types: `object`, `multiobject`

| Attribute | Description |
|-----------|-------------|
| `Related object type` | The type of object this field references. Used for non-polymorphic fields. May be any built-in NetBox object type or another Custom Object Type. |
| `Polymorphic` | When enabled, the field may reference objects of more than one type (uses a generic foreign key). Cannot be changed after the field is created. |
| `Related object types` | For polymorphic fields, the set of object types that may be referenced. Cannot be changed after the field is created. |
| `Related object filter` | A JSON `query_params` dict used to filter the object selection drop-down (e.g. `{"status": "active"}`). |
| `Reverse relation name` | Name for the reverse relation accessor on the related object. For example, setting this to `ssl_profiles` on a Certificate → SLB field allows `slb.ssl_profiles.all()` in export templates. |
| `On delete behavior` | What happens when the referenced object is deleted: `Set null` (clear the field, keep this object), `Cascade` (delete this object too), or `Protect` (prevent deletion of the referenced object). Default: `Set null`. **Applies only to `object` fields, not `multiobject`.** |

!!! note
    To reference another Custom Object Type, choose `Custom Objects > <Custom Object Type name>` in the **Related object type** dropdown. To create a polymorphic field that may reference objects of multiple types, enable **Polymorphic** and select the allowed types under **Related object types**.

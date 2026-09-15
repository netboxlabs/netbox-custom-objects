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
| `url` | URL, with an optional link title |
| `json` | Arbitrary JSON value |
| `select` | Single selection from a choice set |
| `multiselect` | Multiple selections from a choice set |
| `object` | Reference to a single object (built-in NetBox object or Custom Object) |
| `multiobject` | Reference to multiple objects of the same type |
| `coordinates` | A geographic latitude/longitude pair (with a map link) |

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

## Coordinates Fields

Field type: `coordinates`

A single `coordinates` field stores a geographic latitude/longitude pair, mirroring NetBox's
native Site/Device coordinates (plain decimal columns — PostGIS is not required). Adding one
`coordinates` field named `location` creates two backing columns and two form inputs:

- `location_latitude` — decimal, range −90 to 90, up to 6 decimal places
- `location_longitude` — decimal, range −180 to 180, up to 6 decimal places

Behaviour:

- **Both-or-neither.** Latitude and longitude must either both be set or both be empty; setting
  only one is rejected in forms and via the REST API.
- **REST API.** The pair is exposed as two flat fields, `<name>_latitude` and `<name>_longitude`
  (matching NetBox core's serializers), not as a nested object.
- **Map link.** Detail views render the coordinates with a **Map** button that opens the location
  using NetBox's `MAPS_URL` configuration parameter (Google Maps by default).
- `Must be unique` and `Default` are not supported for `coordinates` fields.

## URL Fields

Field type: `url`

A `url` field stores the URL value plus an optional human-readable **link title**.
Adding one `url` field named `website` creates two backing columns:

- `website` — the URL itself
- `website_title` — optional display text shown in place of the raw URL

Behaviour:

- **Detail page and list view.** The title, when set, is used as the visible link
  text both on the object's detail page and in its column in list/table views —
  the two backing columns are never shown as separate table columns. Falls back to
  the raw URL as link text when no title is set.
- **Optional independently.** The title has no relationship to the URL value —
  setting one without the other is allowed and harmless.
- **REST API.** The pair is exposed as two flat fields, `<name>` and `<name>_title`.
- `Must be unique` and `Default` continue to apply to the URL value itself, exactly
  as for any other `url` field.
- **CSV import.** Bulk CSV import only populates the URL value; the title column is
  not importable via CSV (the same limitation applies to `coordinates` fields' backing
  columns).
- **Upgrading from an older release.** Existing `url` fields gain the `<name>_title`
  column automatically on the next `manage.py migrate` (or immediately via
  `manage.py upgrade_custom_objects`) -- this also heals every existing NetBox
  Branching branch's own schema, not just main's. A branch migrating on its own
  afterward re-heals itself too, as a secondary safeguard, but isn't required for
  the column to already be there.

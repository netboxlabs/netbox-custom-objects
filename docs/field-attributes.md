# Custom Object Type Field Attributes

The following attributes are available when creating or editing a Custom Object Type Field. The deprecation attributes at the end of the table are the exception: they can only be set through the [REST API](rest-api.md) or a [portable schema](portable-schema.md) document.

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
| `array` | A list of values of one type, or of fixed-size tuples such as (x, y, z) points |
| `color` | An RGB color, shown as a swatch |
| `mac_address` | An EUI-48 MAC address |

## Common Attributes

| Attribute | Description |
|-----------|-------------|
| `Name` | Internal field name, up to 50 characters. Must be lowercase alphanumeric with single underscores between words (e.g. `rack_unit`), and unique within the Custom Object Type. Some names are reserved because every custom object already uses them, such as `id`, `tags`, `owner`, `created` and `objects`. A name also can't clash with another field's extra columns, such as `<url field>_title` or `<coordinates field>_latitude`. A polymorphic field can't be renamed. |
| `Label` | Human-readable display name shown in the UI, up to 50 characters. Defaults to the field name, with underscores replaced by spaces and the first letter capitalized. |
| `Type` | Data type of the field (see above). A field's type can't be changed to or from `coordinates`, `url`, `array` or `multiobject`, or while the field is part of a constraint. |
| `Description` | Help text shown below the field in forms. |
| `Group name` | Fields sharing the same group name are displayed together. |
| `Required` | When enabled, a value must be provided when creating or editing an object. It can't be enabled while existing objects have no value for the field. |
| `Must be unique` | When enabled, no two objects of this type may share the same value for this field. Not supported for `boolean`, `multiobject` or `coordinates` fields, and can't be enabled while existing objects share a value. To make a combination of fields unique, add a [constraint](index.md#adding-constraints-to-the-custom-object-type) instead. |
| `Primary name field` | When enabled, this field's value is used as the object's display name, unless the type has a display expression. Only one field per type can be primary; enabling it on one field clears it on the others. A field can't be both primary and a context field. |
| `Context field` | When enabled, this field's value is shown as context when this object is referenced by another object. |
| `Default` | Default value pre-populated when creating a new object. Must be a valid JSON value. Setting a default on a `multiobject` field also applies it to every existing object that has no value. |
| `Display weight` | Controls the field's position in forms and detail views; higher weights appear lower. Default: `100`. |
| `Search weight` | Relevance weight for full-text search. Lower values are more important; `0` disables search indexing for this field. Default: `500`. |
| `Filter logic` | `Loose` (match any substring), `Exact` (match whole value), or `Disabled` (no filter). Loose and Exact only differ for text, long text and URL fields. Default: `Loose`. |
| `UI visible` | Controls visibility in detail views: `Always`, `If set`, or `Hidden`. Default: `Always`. |
| `UI editable` | Controls editability in forms: `Yes`, `No` (read-only), or `Hidden`. Default: `Yes`. |
| `Is cloneable` | When enabled, this field's value is copied when cloning an object. |
| `Comments` | Free-form notes about this field (supports Markdown). |
| `Deprecated` | Marks the field as deprecated, e.g. during a grace period before it is removed. Informational only: the field keeps working, and values can still be entered. |
| `Deprecated since` | [PEP 440](https://peps.python.org/pep-0440/) version string indicating the schema version in which the field was deprecated (e.g. `2.0.0`). |
| `Scheduled removal` | [PEP 440](https://peps.python.org/pep-0440/) version string indicating the schema version in which the field is planned to be removed (e.g. `3.0.0`). |

## Text Fields

Field types: `text`, `longtext` (URL fields also take a validation regex)

| Attribute | Description |
|-----------|-------------|
| `Validation regex` | Regular expression enforced on field values. For example, `^[A-Z]{3}$` limits values to exactly three uppercase letters. |

## Numeric Fields

Field types: `integer`, `decimal`

| Attribute | Description |
|-----------|-------------|
| `Minimum value` | Minimum allowed numeric value. |
| `Maximum value` | Maximum allowed numeric value. |

A `decimal` value has up to 16 digits in total, 4 of them after the decimal point, as
for a NetBox decimal custom field.

## Choice Fields

Field types: `select`, `multiselect`

| Attribute | Description |
|-----------|-------------|
| `Choice set` | A NetBox [Custom Field Choice Set](https://netboxlabs.com/docs/netbox/customization/custom-fields/#custom-selection-fields) that defines the available options. Required. |

A choice value may be up to 100 characters long, for `select` and `multiselect` alike.

## Object Reference Fields

Field types: `object`, `multiobject`

| Attribute | Description |
|-----------|-------------|
| `Related object type` | The type of object this field references. Used for non-polymorphic fields. May be any built-in NetBox object type or another Custom Object Type, including this one. Cannot be changed after the field is created. Two Custom Object Types can't reference each other in a cycle. |
| `Polymorphic` | When enabled, the field may reference objects of more than one type (uses a generic foreign key). Cannot be changed after the field is created. |
| `Related object types` | For polymorphic fields, the set of object types that may be referenced. Cannot be changed after the field is created. |
| `Related object filter` | A JSON `query_params` dict used to filter the object selection drop-down (e.g. `{"status": "active"}`). |
| `Reverse relation name` | Name for the reverse relation accessor on the related object. For example, setting this to `ssl_profiles` on a Certificate → SLB field allows `slb.ssl_profiles.all()` in export templates. If left blank, a non-polymorphic `object` field gets a generated name, and `multiobject` and polymorphic fields get no reverse relation. |
| `On delete behavior` | What happens when the referenced object is deleted: `Set null` (clear the field, keep this object), `Cascade` (delete this object too), or `Protect` (prevent deletion of the referenced object). Default: `Set null`. Changing it applies only to later deletes; existing values that already point at a deleted object are left as they are. **Applies only to `object` fields, polymorphic or not, not `multiobject`.** |

!!! note
    To reference another Custom Object Type, choose `Custom Objects > <Custom Object Type name>` in the **Related object type** dropdown. To create a polymorphic field that may reference objects of multiple types, enable **Polymorphic** and select the allowed types under **Related object types**.

## Coordinates Fields

Field type: `coordinates`

A single `coordinates` field stores a geographic latitude/longitude pair, mirroring NetBox's
native Site/Device coordinates (plain decimal columns — PostGIS is not required). Adding one
`coordinates` field named `location` creates two backing columns and two form inputs:

- `location_latitude` — decimal, range −90 to 90, up to 6 decimal places
- `location_longitude` — decimal, range −180 to 180, up to 6 decimal places

Behavior:

- **Both-or-neither.** Latitude and longitude must either both be set or both be empty; setting
  only one is rejected in forms and via the REST API.
- **REST API and CSV import.** The pair is exposed as two flat fields, `<name>_latitude` and
  `<name>_longitude` (matching NetBox core's serializers), not as a nested object.
- **GraphQL.** Coordinates can be filtered on, but aren't returned as fields.
- **Map link.** Detail views render the coordinates with a **Map** button that opens the location
  using NetBox's `MAPS_URL` configuration parameter (Google Maps by default).
- `Must be unique` and `Default` are not supported for `coordinates` fields, and their search weight is always `0`.

## Array Fields

Field type: `array`

An `array` field stores a list of values of one type in a single PostgreSQL array column. Set
**Tuple size** to store a list of fixed-size tuples instead, such as (x, y, z) points.

| Attribute | Description |
|-----------|-------------|
| `Element type` | The type of each value: `Text`, `Integer`, `Decimal`, `Boolean`, `Date` or `Date & time`. Required. Cannot be changed after the field is created. |
| `Tuple size` | Store a list of tuples of this many values (at least 2), e.g. `3` for (x, y, z) points. Leave blank for a list of single values. Cannot be changed after the field is created. |
| `Minimum length` / `Maximum length` | The minimum and maximum number of items (values, or tuples). |
| `Validation regex` | For text elements: a regular expression every value must match. |
| `Minimum value` / `Maximum value` | For integer and decimal elements: the range every value must fall in. |

For example, an `array` field with element type `Integer`, tuple size `3` and minimum length
`2` accepts `[[0, 0, 0], [10, 0, 5]]`, and rejects `[[0, 0]]` (a tuple of the wrong size) and
`[[0, 0, 0]]` (too few items).

Behavior:

- **Forms and CSV import.** Enter the value as JSON, e.g. `[1, 2, 3]` or `[[0, 0, 0], [10, 0, 5]]`;
  dates and times as strings, e.g. `["2026-01-01"]`. In a CSV file, quote the JSON as usual.
- **REST API and GraphQL.** The value is a JSON list (of lists, for tuples).
- **Values.** Each value must be of the element type: an integer array rejects `1.5` and `true`
  rather than storing `1`. An empty list `[]` is stored as no value (`null`), so a required field
  rejects it, and the minimum length applies only to a non-empty list.
- **Display.** Lists show as `1, 2, 3`, and tuples as `(0, 0, 0), (10, 0, 5)`.
- **Filtering.** `?<name>=1,3` matches objects whose array contains all of the comma-separated
  values (for tuples, anywhere in any tuple), and `?<name>__len=2` matches by the number of items.
  Quick search matches objects whose array contains the searched value.
- A field's type cannot be changed to or from `array`, and arrays can't be included in
  [constraints](index.md#adding-constraints-to-the-custom-object-type).

## Color Fields

Field type: `color`

A `color` field stores an RGB color as six lowercase hexadecimal digits without a leading `#`
(e.g. `00ff00`), the same format as NetBox's own color fields such as a tag's color.

- **Forms.** The edit form offers NetBox's named colors. A value set through the REST API or CSV
  import that isn't one of them stays selectable when the object is edited.
- **Default.** The field's default is picked from the same named colors, and the field list shows
  it the same way.
- **Display.** Detail views and tables show a color swatch, like NetBox's own color fields; CSV
  export writes `#rrggbb`, which CSV import accepts along with uppercase digits.
- **Filtering.** Filters match whole values, e.g. `?color=00ff00&color=ff0000`.

## MAC Address Fields

Field type: `mac_address`

A `mac_address` field stores an EUI-48 MAC address in a PostgreSQL `macaddr` column, using NetBox's
own `MACAddressField`, as an interface's MAC address does.

- **Input.** Forms, CSV import and the REST API accept any common format (`00:1a:2b:3c:4d:5e`,
  `00-1A-2B-3C-4D-5E`, `001a.2b3c.4d5e`, `001a2b3c4d5e`) and store the address itself, so every
  format of the same address is equal for uniqueness, constraints and filters.
- **Display.** Values are shown and returned as uppercase, colon-separated hex
  (`00:1A:2B:3C:4D:5E`). The field's default is typed as plain text and shown the same way.
- **Filtering.** `?mac=00:1a:2b:3c:4d:5e&mac=...` matches whole addresses in any format, and the
  usual suffixes such as `__ic` match part of one. Quick search matches part of an address.

## URL Fields

Field type: `url`

A `url` field stores the URL value plus an optional human-readable **link title**.
Adding one `url` field named `website` creates two backing columns:

- `website` — the URL itself
- `website_title` — optional display text shown in place of the raw URL

Behavior:

- **Detail page and list view.** The title, when set, is used as the visible link
  text both on the object's detail page and in its column in list/table views —
  the two backing columns are never shown as separate table columns. Falls back to
  the raw URL as link text when no title is set.
- **Optional independently.** The title has no relationship to the URL value —
  setting one without the other is allowed and harmless.
- **REST API and CSV import.** The pair is exposed as two flat fields, `<name>` and `<name>_title`.
- **Validation regex.** As for text fields, a regular expression the URL must match.
- `Must be unique` and `Default` continue to apply to the URL value itself, exactly
  as for any other `url` field.
- **GraphQL.** Only the URL is returned; the title isn't available in GraphQL.

# REST API

The NetBox Custom Objects plugin exposes full CRUD operations through the standard NetBox REST API, rooted at `/api/plugins/custom-objects/`. The root of the API lists endpoints for Custom Object Types, Custom Object Type Fields, the linked-objects helper, and a dynamically generated endpoint for each Custom Object Type you have defined:

```json
{
    "custom-object-types": "https://netbox/api/plugins/custom-objects/custom-object-types/",
    "custom-object-type-fields": "https://netbox/api/plugins/custom-objects/custom-object-type-fields/",
    "linked-objects": "https://netbox/api/plugins/custom-objects/linked-objects/",
    "dhcp_scope": "https://netbox/api/plugins/custom-objects/dhcp_scope/"
}
```

The endpoint name for each Custom Object Type is its **slug** — a type with slug `dhcp_scope` is reachable at `/api/plugins/custom-objects/dhcp_scope/`.

!!! tip "Portable schema endpoints"
    In addition to per-type CRUD, the plugin exposes schema management at:

    | Method | Path | Purpose |
    |--------|------|---------|
    | `GET` | `/api/plugins/custom-objects/schema/export/` | Export COT **type** definitions (`types` only) |
    | `POST` | `/api/plugins/custom-objects/schema/preview/` | Diff preview (no DB writes) |
    | `POST` | `/api/plugins/custom-objects/schema/apply/` | Apply types; optional `choice_sets` and `objects` |

    Full format, examples, and error codes: [Portable Schema](portable-schema.md).
    Reference demo document:
    `netbox_custom_objects/schema/examples/security_objects.json`.

## Custom Object Types

Create a Custom Object Type with a `POST` to `/api/plugins/custom-objects/custom-object-types/`:

```json
{
  "name": "server",
  "slug": "server",
  "description": "Server inventory objects",
  "verbose_name": "Server",
  "verbose_name_plural": "Servers"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Internal name. Must be lowercase alphanumeric with underscores only (e.g. `dhcp_scope`). Must be unique. |
| `slug` | yes | URL-safe slug used in API paths and navigation. Must be unique. |
| `verbose_name` | no | Singular display name shown in the UI. Defaults to the title-cased `name`. |
| `verbose_name_plural` | no | Plural display name. Defaults to `verbose_name` + `"s"`. |
| `description` | no | Short description (max 200 characters). |
| `version` | no | [PEP 440](https://peps.python.org/pep-0440/) version string (e.g. `1.0.0`). Used by the portable schema feature. |
| `group_name` | no | Groups similar Custom Object Types together in the navigation menu. |
| `tags` | no | List of NetBox tag IDs to attach to this Custom Object Type. |

The Custom Object Types **list view** (UI only) adds two columns that help plan
deletions before calling the API:

| Column | Description |
|--------|-------------|
| **Objects** | Count of object instances for the type. Linked to the type's object list when non-zero. |
| **Referenced by** | Count of other Custom Object Types whose schema references this type (`related_object_type` or polymorphic `related_object_types`). Hover shows the referrer names. |

These mirror the blocking checks enforced by `DELETE` (see below).

### Deleting Custom Object Types

`DELETE /api/plugins/custom-objects/custom-object-types/<id>/` is rejected while the type
still has object instances or while another Custom Object Type's schema references it
(`related_object_type` or polymorphic `related_object_types`). The API returns a blocking
error instead of silently removing cross-type references. Delete or migrate all instances
first, then remove dependent types before the types they reference. See
[Deleting Custom Object Types](portable-schema.md#deleting-custom-object-types) in the
portable schema guide for details and examples.

## Custom Object Type Fields

Define the schema of a Custom Object Type by creating fields with POST requests to `/api/plugins/custom-objects/custom-object-type-fields/`, referencing the ID of the Custom Object Type:

```json
{
  "custom_object_type": 9,
  "name": "internal_id",
  "label": "Internal ID",
  "type": "integer",
  "required": true,
  "validation_minimum": 0,
  "validation_maximum": 9999
}
```

Available `type` values are:

| Type | Description |
|------|-------------|
| `text` | Short text field |
| `longtext` | Long text field with textarea widget |
| `integer` | Integer number |
| `decimal` | Decimal number |
| `boolean` | True/false |
| `date` | Date |
| `datetime` | Date and time |
| `url` | URL |
| `json` | JSON value |
| `select` | Single selection from a choice set |
| `multiselect` | Multiple selections from a choice set |
| `object` | Reference to a single object |
| `multiobject` | Reference to multiple objects of the same type |

Common attributes available on all field types:

| Attribute | Required | Description |
|-----------|----------|-------------|
| `name` | yes | Internal field name. Must be lowercase alphanumeric with underscores only (e.g. `rack_unit`). Must be unique within the Custom Object Type. |
| `type` | yes | Field type (see table above). |
| `label` | no | Display name shown in the UI. Defaults to `name`. |
| `description` | no | Help text shown in forms. |
| `group_name` | no | Fields sharing the same group name are displayed together. |
| `required` | no | Whether a value must be provided. Default: `false`. |
| `unique` | no | Whether values must be unique across all objects of this type. Default: `false`. Not supported for `boolean` or `multiobject` fields. |
| `primary` | no | Whether this field's value is used as the object's display name. |
| `context` | no | Whether this field's value is shown when the object is referenced by another object. |
| `default` | no | Default value (must be a valid JSON value). |
| `weight` | no | Display order weight. Higher values appear lower. Default: `100`. |
| `search_weight` | no | Search relevance weight. Lower values are more important; `0` disables search indexing. Default: `500`. |
| `filter_logic` | no | `"loose"` (substring match), `"exact"` (exact match), or `"disabled"`. Default: `"loose"`. |
| `ui_visible` | no | `"always"`, `"if-set"`, or `"hidden"`. Default: `"always"`. |
| `ui_editable` | no | `"yes"`, `"no"` (read-only), or `"hidden"`. Default: `"yes"`. |
| `is_cloneable` | no | Whether the field value is copied when cloning an object. Default: `false`. |
| `comments` | no | Free-form notes about this field (supports Markdown). |
| `deprecated` | no | Marks the field as deprecated (read-only in the UI). Default: `false`. |
| `deprecated_since` | no | [PEP 440](https://peps.python.org/pep-0440/) version string when the field was deprecated (e.g. `"2.0.0"`). |
| `scheduled_removal` | no | [PEP 440](https://peps.python.org/pep-0440/) version string when the field is planned for removal (e.g. `"3.0.0"`). |
| `schema_id` | (read-only) | Stable, auto-assigned identifier used by the portable schema feature. See [Portable Schema](portable-schema.md). |

### Text Fields

Field types: `text`, `longtext`

```json
{
  "custom_object_type": 9,
  "name": "hostname",
  "label": "Hostname",
  "type": "text",
  "required": true,
  "validation_regex": "^[a-zA-Z0-9-]+$"
}
```

| Attribute | Description |
|-----------|-------------|
| `validation_regex` | Regular expression enforced on field values. |

### Numeric Fields

Field types: `integer`, `decimal`

```json
{
  "custom_object_type": 9,
  "name": "cpu_cores",
  "label": "CPU Cores",
  "type": "integer",
  "validation_minimum": 1,
  "validation_maximum": 128
}
```

| Attribute | Description |
|-----------|-------------|
| `validation_minimum` | Minimum allowed value. |
| `validation_maximum` | Maximum allowed value. |

### Choice Fields

Field types: `select`, `multiselect`

```json
{
  "custom_object_type": 9,
  "name": "environment",
  "label": "Environment",
  "type": "select",
  "choice_set": 5
}
```

| Attribute | Description |
|-----------|-------------|
| `choice_set` | ID of a NetBox Custom Field Choice Set. Required. |

### Object Reference Fields

Field types: `object`, `multiobject`

For non-polymorphic `object` or `multiobject` fields, specify the content type using `app_label` and `model`:

```json
{
  "custom_object_type": 9,
  "name": "primary_device",
  "label": "Primary Device",
  "type": "object",
  "app_label": "dcim",
  "model": "device"
}
```

```json
{
  "custom_object_type": 9,
  "name": "device_list",
  "label": "Device List",
  "type": "multiobject",
  "app_label": "dcim",
  "model": "device"
}
```

Additional attributes for object reference fields:

| Attribute | Description |
|-----------|-------------|
| `app_label` | Django app label of the related model (write-only). Required for non-polymorphic object fields when `related_object_type` is not supplied directly. |
| `model` | Model name of the related model (write-only). Required alongside `app_label`. |
| `related_object_type` | (read-only) Nested representation of the referenced object type. |
| `is_polymorphic` | When `true`, the field accepts references to objects of multiple different types. Requires `related_object_types_input` instead of `app_label`/`model`. Cannot be changed after the field is created. Default: `false`. |
| `related_object_types_input` | List of `{"app_label": ..., "model": ...}` dicts (write-only). Required for polymorphic fields. |
| `related_object_types` | (read-only) Nested representation of the allowed object types for polymorphic fields. |
| `related_object_filter` | JSON `query_params` dict used to filter the object selection drop-down. |
| `related_name` | Reverse relation accessor name on the related object (e.g. `ssl_profiles` allows `obj.ssl_profiles.all()`). |
| `on_delete_behavior` | Action when the referenced object is deleted: `"set_null"` (default), `"cascade"`, or `"protect"`. **Applies only to `object` fields; ignored on `multiobject`.** |

!!! note
    An `object` or `multiobject` field can reference any Custom Object Type as well as any core NetBox object. To reference another Custom Object Type, set `app_label` to `"custom-objects"` and `model` to the target Custom Object Type's slug. For example:

    ```json
    {
      "custom_object_type": 9,
      "name": "parent_circuit",
      "type": "object",
      "app_label": "custom-objects",
      "model": "circuit"
    }
    ```

### Polymorphic Object Reference Fields

A polymorphic `object` or `multiobject` field can reference objects of multiple different types. Set `is_polymorphic: true` and provide the allowed types via `related_object_types_input`:

```json
{
  "custom_object_type": 9,
  "name": "linked_resource",
  "label": "Linked Resource",
  "type": "object",
  "is_polymorphic": true,
  "related_object_types_input": [
    {"app_label": "dcim", "model": "device"},
    {"app_label": "dcim", "model": "rack"},
    {"app_label": "custom-objects", "model": "server"}
  ]
}
```

!!! note
    The `is_polymorphic` flag and the set of allowed `related_object_types` cannot be changed after the field is created. To convert between a polymorphic and a non-polymorphic field, delete and recreate the field.

## Custom Objects

Once a Custom Object Type's schema is defined, create Custom Objects (instances) with a `POST` to `/api/plugins/custom-objects/<slug>/`, where `<slug>` is the slug of the Custom Object Type:

```json
{
  "internal_id": 102,
  "hostname": "server-001",
  "cpu_cores": 8,
  "environment": "production",
  "device_list": [34, 1],
  "primary_device": 16
}
```

For non-polymorphic `object` and `multiobject` fields, pass the primary key (integer) of the referenced object — or a list of primary keys, for `multiobject`. For **polymorphic** fields, pass a dict identifying both the content type and the object:

```json
{
  "linked_resource": {"app_label": "dcim", "model": "device", "object_id": 7}
}
```

`content_type_id` may be used in place of `app_label` + `model`, and `id` may be used as an alias for `object_id` so that the read representation can be round-tripped directly back as a write payload.

The response includes the created object with its assigned ID and standard metadata:

```json
{
  "id": 15,
  "url": "https://netbox/api/plugins/custom-objects/server/15/",
  "custom_object_type": {
    "id": 9,
    "name": "server",
    "description": "Server inventory objects"
  },
  "internal_id": 102,
  "hostname": "server-001",
  "cpu_cores": 8,
  "environment": "production",
  "device_list": [34, 1],
  "primary_device": 16,
  "tags": [],
  "created": "2024-01-15T10:30:00Z",
  "last_updated": "2024-01-15T10:30:00Z"
}
```

## Custom Validation

NetBox's [`CUSTOM_VALIDATORS`](https://netboxlabs.com/docs/netbox/en/stable/configuration/data-validation/#custom_validators) setting is supported for Custom Objects. Use `netbox_custom_objects.<cot-slug>` as the key, where `<cot-slug>` is the slug of the Custom Object Type:

```python
# configuration.py
CUSTOM_VALIDATORS = {
    "netbox_custom_objects.server": [
        {
            "hostname": {"min_length": 3, "max_length": 64},
            "cpu_cores": {"min": 1},
        }
    ]
}
```

Validators are enforced on both API writes and UI form submissions. Any violation raises an HTTP 400 error (API) or a form validation error (UI).

!!! note
    The key must use the Custom Object Type **slug** (e.g. `server`), not the type's name or its internal model name.

## Linked Objects

Any NetBox object — whether a core object such as Device or Site, or a Custom Object — can be referenced by one or more Custom Objects via `object` or `multiobject` fields. To retrieve all Custom Objects that link to a given object, query the linked-objects endpoint:

```
GET /api/plugins/custom-objects/linked-objects/?object_type=<object_type>&object_id=<object_id>
```

Both query parameters are required:

| Parameter | Description |
|-----------|-------------|
| `object_type` | Target model in `app_label.model` form, e.g. `dcim.device`. |
| `object_id` | Primary key of the target object. |

Example response:

```json
{
    "count": 1,
    "results": [
        {
            "custom_object_type": {"id": 1, "name": "My Type", "slug": "my-type"},
            "field_name": "device",
            "object": {"id": 7, "display": "My Custom Object"}
        }
    ]
}
```

## Browsable API

As with other NetBox objects, you can view the API output for Custom Objects in a browser by prepending `/api/` to the URL — for example, `/api/plugins/custom-objects/dhcp_scope/`:

```
HTTP 200 OK
Allow: GET, POST, HEAD, OPTIONS
Content-Type: application/json
Vary: Accept

{
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
        {
            "id": 1,
            "url": "http://localhost:8001/api/plugins/custom-objects/dhcp_scope/1/",
            "range": {
                "id": 1,
                "url": "http://localhost:8001/api/ipam/ip-ranges/1/",
                "display": "192.168.0.1-100/24",
                "family": {
                    "value": 4,
                    "label": "IPv4"
                },
                "start_address": "192.168.0.1/24",
                "end_address": "192.168.0.100/24",
                "description": ""
            }
        }
    ]
}
```

## Other Operations

`GET`, `PUT`, `PATCH`, and `DELETE` requests are supported on all of the above, using the detail URL for each object:

| Resource | Detail URL |
|----------|------------|
| Custom Object Type | `/api/plugins/custom-objects/custom-object-types/<id>/` |
| Custom Object Type Field | `/api/plugins/custom-objects/custom-object-type-fields/<id>/` |
| Custom Object | `/api/plugins/custom-objects/<slug>/<id>/` |

Standard NetBox filter parameters (e.g. `q=`, `tag=`, `created__gte=`) work against the list endpoints. Each Custom Object Type also exposes filters for every defined field — see the OpenAPI schema at `/api/schema/swagger-ui/` for the full list of filters available on a given type.

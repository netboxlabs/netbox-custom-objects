# Portable Schema

The portable schema feature allows Custom Object Type (COT) definitions to be exported as
structured JSON documents, versioned in source control, and applied to other NetBox instances.
This makes COT schemas shareable, auditable, and deployable across environments in a consistent
and repeatable way.

## Concepts

### Schema documents

A schema document is a JSON object that fully describes one or more Custom Object Types —
their names, metadata, and all field definitions. The document is self-contained: a reader
does not need access to the originating NetBox instance to understand or validate it.

```json
{
  "schema_version": "1",
  "types": [
    {
      "name": "circuit",
      "slug": "circuit",
      "verbose_name": "Circuit",
      "verbose_name_plural": "Circuits",
      "description": "WAN circuit inventory",
      "fields": [
        { "id": 1, "name": "carrier", "type": "text", "required": true },
        { "id": 2, "name": "bandwidth_mbps", "type": "integer", "validation_minimum": 0 }
      ],
      "removed_fields": []
    }
  ]
}
```

### Schema IDs

Every `CustomObjectTypeField` carries a `schema_id` — a stable, monotonically increasing integer
scoped to its parent COT. Schema IDs are the primary key used by the comparator and executor to
match fields across export and apply cycles:

- Assigned automatically on first save if not set explicitly.
- Scoped per COT — field 3 in COT "circuit" is unrelated to field 3 in COT "device-profile".
- **Never reused.** When a field is deleted its `schema_id` is retired. The parent COT's
  `next_schema_id` counter only ever advances, even if fields are removed.
- Stable across renames — a field retains its `schema_id` when its `name` attribute changes,
  so the comparator correctly identifies a rename rather than a deletion plus addition.
- Assigned atomically using `SELECT ... FOR UPDATE` on the parent COT row to prevent
  race conditions under concurrent field creation.

> **Note for bulk operations:** `bulk_create()` bypasses the model `save()` method and will
> not auto-assign `schema_id`. Callers that use `bulk_create()` must set `schema_id`
> explicitly on each field instance.

### Tombstones

When a field is removed from a COT, the comparator needs to distinguish "this field was
intentionally deleted" from "this field is not in the schema yet." Tombstone entries in
`removed_fields` provide that signal:

```json
"removed_fields": [
  { "id": 4, "name": "legacy_carrier_code", "type": "text", "removed_in": "2.0.0" }
]
```

A tombstone records the field's last-known `id`, `name`, `type`, and the version string when
it was removed. The executor uses tombstones to drop the corresponding DB column; without a
tombstone the comparator treats the absent field as ambiguous and emits a warning rather than
a `REMOVE`.

Tombstones are persisted in the `CustomObjectType.schema_document` field and read back into
every subsequent export, so the full removal history accumulates over time.

### Schema document storage

`CustomObjectType.schema_document` stores the most recently applied or exported schema
snapshot as a JSON blob. It is written by the executor after a successful apply, and read
by the exporter when building tombstone lists. Until a COT has been exported or a schema
applied, the field is `null`.

---

## Design decisions

### Integer `schema_id` instead of UUID

Several alternatives were considered for the stable field identity value:

| Approach | Pros | Cons |
|----------|------|------|
| **Monotonic integer** (chosen) | Human-readable diffs; encodes creation order; no collision risk in single-source model | False-matches if two instances independently assign the same ID to different fields |
| UUID | Globally unique; safe for peer-merge scenarios | Opaque in JSON diffs; no ordering information |

Integers were chosen because the intended workflow is **single-source-of-truth**: one canonical
environment exports schemas, and downstream instances receive and apply them. In this hub-and-spoke
model there is no opportunity for two instances to independently create fields and generate
conflicting IDs, so UUID collision avoidance is unnecessary.

This follows the same convention as Protocol Buffers, which also uses integer field IDs and
places the responsibility for incrementing them on the schema author.

> **Important constraint:** if the product ever requires bidirectional sync or multi-master
> schema evolution — where two environments independently evolve the same COT and their schemas
> need to be reconciled — integer IDs would need to be replaced with UUIDs. The current design
> does not support that workflow.

### Single-source-of-truth distribution model

The feature is designed around a hub-and-spoke topology:

1. A **canonical environment** (e.g., a development instance or a dedicated schema registry)
   defines and maintains COT schemas.
2. Schema documents are exported and committed to version control.
3. **Downstream instances** (staging, production) receive schema documents and apply them via
   the API.

The comparator and executor handle the downstream side: they diff an incoming schema against
the live DB state and apply the delta atomically. There is no merge logic for reconciling
independent changes from multiple peers.

### Identifier pattern alignment

COT names (`CustomObjectType.name`) and field names (`CustomObjectTypeField.name`) must satisfy
the pattern `^[a-z0-9]+(_[a-z0-9]+)*$`. This is the same pattern used by the JSON Schema
`identifier` definition, ensuring that any name accepted by the database will also pass schema
validation and round-trip cleanly through export and apply cycles.

The pattern permits:
- `circuit`, `bandwidth_mbps`, `carrier_code_v2`

The pattern rejects:
- `_private` (leading underscore)
- `foo_` (trailing underscore)
- `test__field` (double underscore)
- `my-field` (hyphen)

---

## Schema document format

The JSON Schema validator for schema documents lives at
`netbox_custom_objects/schemas/cot_schema_v1.json` and is used by the API endpoints to
validate incoming documents before any DB access.

### Top-level structure

| Key | Type | Description |
|-----|------|-------------|
| `schema_version` | `"1"` | Format version. Currently only `"1"` is supported. |
| `types` | array of COT definitions | One entry per Custom Object Type. |

### COT definition

| Key | Required | Description |
|-----|----------|-------------|
| `name` | yes | Internal name, must match identifier pattern. |
| `slug` | yes | URL-safe slug. Used as the stable lookup key when applying. |
| `verbose_name` | no | Singular display name. |
| `verbose_name_plural` | no | Plural display name. |
| `version` | no | Free-form version string for the COT schema (e.g. `"2.1.0"`). |
| `description` | no | Short description. |
| `fields` | yes | Array of active field definitions. |
| `removed_fields` | no | Array of tombstone records for previously removed fields. |

### Field definition

All fields share these base attributes:

| Key | Required | Description |
|-----|----------|-------------|
| `id` | yes | Stable integer schema ID (>= 1). |
| `name` | yes | Internal field name, must match identifier pattern. |
| `type` | yes | Field type (see below). |
| `label` | no | Display label. Defaults to `name` with underscores replaced by spaces. |
| `description` | no | Help text shown in the UI. |
| `group_name` | no | UI grouping. |
| `primary` | no | Whether this is the primary display field. |
| `required` | no | Whether the field is required. Default: `false`. |
| `unique` | no | Whether values must be unique. Default: `false`. |
| `default` | no | JSON default value. |
| `weight` | no | Display order weight. Default: `100`. |
| `search_weight` | no | Search relevance weight. Default: `500`. |
| `filter_logic` | no | `"loose"`, `"exact"`, or `"disabled"`. Default: `"loose"`. |
| `ui_visible` | no | `"always"`, `"if-set"`, or `"hidden"`. Default: `"always"`. |
| `ui_editable` | no | `"yes"`, `"no"`, or `"hidden"`. Default: `"yes"`. |
| `is_cloneable` | no | Whether the field is copied when cloning objects. Default: `false`. |
| `deprecated` | no | Marks the field as deprecated. Default: `false`. |
| `deprecated_since` | no | Version string when the field was deprecated (e.g. `"2.0.0"`). |
| `scheduled_removal` | no | Version string when the field is planned for removal (e.g. `"3.0.0"`). |

Attributes that match their defaults are omitted from exported documents to keep output minimal.

### Field types and type-specific attributes

| Type | Additional attributes |
|------|-----------------------|
| `text`, `longtext` | `validation_regex` |
| `integer`, `decimal` | `validation_minimum`, `validation_maximum` |
| `select`, `multiselect` | `choice_set` (required — name of a `CustomFieldChoiceSet`) |
| `object`, `multiobject` | `related_object_type` (required), `related_object_filter` |
| `boolean`, `date`, `datetime`, `url`, `json` | (none) |

### Related object type encoding

`related_object_type` is encoded as a `/`-separated string:

- **Built-in NetBox model:** `"dcim/device"`, `"ipam/prefix"`
- **Custom Object Type:** `"custom-objects/<cot-slug>"`, e.g. `"custom-objects/circuit"`

### Tombstone record

```json
{
  "id": 4,
  "name": "legacy_carrier_code",
  "type": "text",
  "removed_in": "2.0.0"
}
```

`removed_in` is optional but recommended. The `id` value must match the original field's
`schema_id` and must not appear in the active `fields` list.

---

## Usage

### Exporting a schema

Use the Python API from the `exporter` module. This is typically called from a management
command or script:

```python
from netbox_custom_objects.exporter import export_cots
from netbox_custom_objects.models import CustomObjectType

cots = CustomObjectType.objects.filter(slug__in=["circuit", "device-profile"])
document = export_cots(cots)

import json
print(json.dumps(document, indent=2))
```

`export_cots` returns a dict with `schema_version` and `types`. For a single COT without the
document wrapper, use `export_cot(cot)`.

> **Fields without a `schema_id`** (created before the portable schema feature was introduced)
> are skipped with a `WARNING` log entry. Run the backfill migration (see below) to assign IDs
> to pre-existing fields.

### Previewing a schema (API)

`POST /api/plugins/custom-objects/schema/preview/`

Submit a schema document and receive a structured diff showing what would change, **without
modifying the database**:

```http
POST /api/plugins/custom-objects/schema/preview/
Content-Type: application/json
Authorization: Token <token>

{
  "schema_version": "1",
  "types": [
    {
      "name": "circuit",
      "slug": "circuit",
      "verbose_name_plural": "Circuits",
      "fields": [
        { "id": 1, "name": "carrier", "type": "text", "required": true },
        { "id": 3, "name": "contract_ref", "type": "text" }
      ],
      "removed_fields": [
        { "id": 2, "name": "bandwidth_mbps", "type": "integer", "removed_in": "2.0.0" }
      ]
    }
  ]
}
```

Response `200`:

```json
{
  "diffs": [
    {
      "slug": "circuit",
      "name": "circuit",
      "is_new": false,
      "has_changes": true,
      "has_destructive_changes": true,
      "cot_changes": {},
      "field_changes": [
        {
          "op": "add",
          "schema_id": 3,
          "db_name": null,
          "schema_def": { "id": 3, "name": "contract_ref", "type": "text" }
        },
        {
          "op": "remove",
          "schema_id": 2,
          "db_name": "bandwidth_mbps",
          "schema_def": { "id": 2, "name": "bandwidth_mbps", "type": "integer", "removed_in": "2.0.0" }
        }
      ],
      "warnings": []
    }
  ]
}
```

`has_destructive_changes: true` indicates that applying this schema would drop at least one
column. The preview endpoint never returns `409` — it is safe to call at any time.

### Applying a schema (API)

`POST /api/plugins/custom-objects/schema/apply/`

```http
POST /api/plugins/custom-objects/schema/apply/
Content-Type: application/json
Authorization: Token <token>

{
  "allow_destructive": false,
  "schema": { ... }
}
```

- **`allow_destructive`** (default `false`): must be `true` for the apply to proceed when the
  diff contains `REMOVE` operations. If `false` and removals are present, the endpoint returns
  `409 Conflict`.
- The apply is **fully atomic** — a failure at any point rolls back all changes including newly
  created COT tables (PostgreSQL supports transactional DDL).
- On success, `schema_document` is persisted on each affected COT so tombstones are available
  for future export/diff cycles.

Response `200`:

```json
{
  "applied": true,
  "diffs": [ ... ]
}
```

Response `409 Conflict`:

```json
{
  "error": "destructive_changes",
  "detail": "Schema contains destructive field removals for COT(s): circuit.",
  "destructive_slugs": ["circuit"]
}
```

Response `400 Bad Request` (invalid schema, unresolvable reference, or circular COT dependency):

```json
{
  "error": "unresolvable_reference",
  "detail": "..."
}
```

### Typical end-to-end workflow

1. **Define and iterate** on COT schemas in a development environment using the NetBox UI or
   API.
2. **Export** the schemas to a JSON file and commit to version control.
3. **Review** the diff in the PR — because IDs are stable integers and defaults are elided,
   the diff is human-readable.
4. **Preview** the schema on a staging instance using the preview endpoint to confirm the diff
   matches expectations.
5. **Apply** the schema on staging (and then production), using `allow_destructive: true` only
   when column drops have been explicitly reviewed.

---

## Field deprecation lifecycle

Fields can be marked deprecated without being removed, allowing a grace period before deletion:

```json
{
  "id": 5,
  "name": "old_carrier_name",
  "type": "text",
  "deprecated": true,
  "deprecated_since": "2.1.0",
  "scheduled_removal": "3.0.0"
}
```

- `deprecated: true` marks the field as read-only in the UI; no new values can be entered.
- `deprecated_since` is an informational version string (no format enforced).
- `scheduled_removal` signals to consumers when the field will be tombstoned.

Deprecation is non-destructive. The field remains in `fields` (not `removed_fields`) until it
is actually deleted, at which point a tombstone entry should be added.

---

## Comparator (developer reference)

`netbox_custom_objects/comparator.py` — pure-read, no DB writes.

```python
from netbox_custom_objects.comparator import diff_document, diff_cot

diffs = diff_document(schema_doc)   # list[COTDiff]
diff  = diff_cot(type_def)          # COTDiff
```

### `COTDiff`

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | COT name from the schema document |
| `slug` | `str` | Lookup key (COT is matched by slug) |
| `is_new` | `bool` | `True` if no COT with this slug exists in the DB |
| `cot_changes` | `dict[str, tuple]` | `{attr: (db_value, schema_value)}` for changed top-level attributes |
| `field_changes` | `list[FieldChange]` | Per-field operations |
| `warnings` | `list[str]` | Non-fatal issues (untracked fields, ambiguous absences) |

Convenience properties: `has_changes`, `has_destructive_changes`, `adds`, `removes`, `alters`.

### `FieldChange`

| Attribute | Type | Description |
|-----------|------|-------------|
| `op` | `FieldOp` | `ADD`, `REMOVE`, or `ALTER` |
| `schema_id` | `int` | Stable field identifier |
| `db_name` | `str \| None` | Current DB field name (`None` for `ADD`) |
| `schema_def` | `dict` | Raw field dict from the schema document |
| `changed_attrs` | `dict[str, tuple]` | `{attr: (db_value, schema_value)}` for `ALTER` operations |

Properties: `is_rename`, `is_type_change`.

### Matching rules

- Fields are matched exclusively by `schema_id`. DB fields with no `schema_id` generate a
  **warning**, not a `REMOVE`.
- `REMOVE` is emitted only when a `schema_id` appears in the document's `removed_fields`.
  A field absent from both `fields` and `removed_fields` generates a **warning**.

---

## Executor (developer reference)

`netbox_custom_objects/executor.py` — writes to the DB.

```python
from netbox_custom_objects.executor import apply_document, apply_diffs

diffs = apply_document(schema_doc, allow_destructive=False)  # list[COTDiff]
apply_diffs(diffs, type_defs_by_slug, allow_destructive=False)  # lower-level
```

`apply_document` is the primary entry point. `apply_diffs` is available when diffs have been
pre-computed by the comparator (e.g. for preview-then-apply flows).

All DB writes are wrapped in a single `transaction.atomic()` block. Any exception causes a
full rollback.

### Exceptions

| Exception | Raised when |
|-----------|-------------|
| `DestructiveChangesError` | `REMOVE` operations are present and `allow_destructive=False` |
| `CircularDependencyError` | Cross-COT `related_object_type` references form a cycle among new COTs |
| `UnknownChoiceSetError` | A `choice_set` name cannot be resolved |
| `UnknownObjectTypeError` | A `related_object_type` string cannot be resolved |

`DestructiveChangesError` is raised **before** the transaction opens, so the DB is never
touched. The other exceptions may be raised mid-transaction, triggering a full rollback.

### Dependency ordering

When a schema document contains multiple new COTs that reference each other via
`related_object_type: "custom-objects/<slug>"`, the executor performs a topological sort to
ensure referenced COT tables exist before any referencing field is added. Cycles among new
COTs raise `CircularDependencyError`.

---

## Backfilling pre-existing fields

Fields created before the portable schema feature was introduced have `schema_id = null`.
Migration `0007_backfill_schema_ids` assigns IDs to all such fields in PK order and updates
each COT's `next_schema_id` counter accordingly. This migration runs automatically with
`manage.py migrate`.

After the backfill, all existing fields participate in export and diff cycles normally.

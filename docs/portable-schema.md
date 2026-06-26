# Portable Schema

The portable schema feature allows Custom Object Type (COT) definitions to be exported as
structured JSON documents, versioned in source control, and applied to other NetBox instances.
This makes COT schemas shareable, auditable, and deployable across environments in a consistent
and repeatable way.

## Concepts

### Schema Documents

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

### Schema Document Storage

`CustomObjectType.schema_document` stores the most recently applied or exported schema
snapshot as a JSON blob. It is written by the executor after a successful apply, and read
by the exporter when building tombstone lists. Until a COT has been exported or a schema
applied, the field is `null`.

---

## Design Decisions

### Integer `schema_id` Instead of UUID

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

### Single-Source-of-Truth Distribution Model

The feature is designed around a hub-and-spoke topology:

1. A **canonical environment** (e.g., a development instance or a dedicated schema registry)
   defines and maintains COT schemas.
2. Schema documents are exported and committed to version control.
3. **Downstream instances** (staging, production) receive schema documents and apply them via
   the API.

The comparator and executor handle the downstream side: they diff an incoming schema against
the live DB state and apply the delta atomically. There is no merge logic for reconciling
independent changes from multiple peers.

### Identifier Pattern Alignment

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

## Schema Document Format

The JSON Schema validator for schema documents lives at
`netbox_custom_objects/schema/cot_schema_v1.json` and is used by the API endpoints to
validate incoming documents before any DB access.

### Top-Level Structure

Recommended key order when authoring documents by hand:
`schema_version` → `choice_sets` → `types` → `objects`.

| Key | Type | Description |
|-----|------|-------------|
| `schema_version` | `"1"` | Format version. Currently only `"1"` is supported. |
| `types` | array of COT definitions | One entry per Custom Object Type. **Required.** |
| `choice_sets` | array of choice set definitions | Optional. Created/updated on apply before types. Not exported. |
| `objects` | array of object group definitions | Optional. Instance seed data upserted on apply after types. Not exported. |

### COT Definition

| Key | Required | Description |
|-----|----------|-------------|
| `name` | yes | Internal name, must match identifier pattern. |
| `slug` | yes | URL-safe slug. Used as the stable lookup key when applying. |
| `verbose_name` | no | Singular display name. |
| `verbose_name_plural` | no | Plural display name. |
| `version` | no | [PEP 440](https://peps.python.org/pep-0440/) version string for the schema (e.g. `"2.1.0"`). |
| `description` | no | Short description (max 200 characters). |
| `group_name` | no | Navigation menu grouping. |
| `fields` | yes | Array of active field definitions. |
| `removed_fields` | no | Array of tombstone records for previously removed fields. |

!!! note
    The `comments` attribute on Custom Object Types and Custom Object Type Fields is intentionally **excluded** from the schema document format. It is editorial annotation rather than structural schema, and including it would create noise in diffs and across-installation sharing.

### Field Definition

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
| `deprecated_since` | no | [PEP 440](https://peps.python.org/pep-0440/) version string when the field was deprecated (e.g. `"2.0.0"`). |
| `scheduled_removal` | no | [PEP 440](https://peps.python.org/pep-0440/) version string when the field is planned for removal (e.g. `"3.0.0"`). |

Attributes that match their defaults are omitted from exported documents to keep output minimal.

### Field Types and Type-Specific Attributes

| Type | Additional attributes |
|------|-----------------------|
| `text`, `longtext` | `validation_regex` |
| `integer`, `decimal` | `validation_minimum`, `validation_maximum` |
| `select`, `multiselect` | `choice_set` (required — **name** of a `CustomFieldChoiceSet`; the set must exist or be listed under document-level `choice_sets`) |
| `object` | `related_object_type` (required when `is_polymorphic` is `false`), `related_object_types` (required when `is_polymorphic` is `true`), `is_polymorphic`, `related_object_filter`, `on_delete_behavior` |
| `multiobject` | `related_object_type` (required when `is_polymorphic` is `false`), `related_object_types` (required when `is_polymorphic` is `true`), `is_polymorphic`, `related_object_filter` |
| `boolean`, `date`, `datetime`, `url`, `json` | (none) |

`on_delete_behavior` is one of `"set_null"` (default), `"cascade"`, or `"protect"`. It applies only to single `object` fields.

`is_polymorphic` defaults to `false`. When `true`, the field uses a generic foreign key and accepts references to any of the types listed in `related_object_types`. The `is_polymorphic` flag and the set of allowed types cannot be changed after the field is created.

### Choice Sets (optional)

Documents may include a top-level `choice_sets` array so that apply can create or update
`CustomFieldChoiceSet` rows before COT fields are written. Export does not include this
section — add it when sharing schemas that introduce new sets.

```json
{
  "schema_version": "1",
  "choice_sets": [
    {
      "name": "security_object_status",
      "choices": ["active", "reserved", "deprecated"]
    }
  ],
  "types": [ ... ]
}
```

Each entry requires `name` (matches field `choice_set` references) and `choices` (non-empty
list of unique strings). On apply, missing sets are created; existing sets get their
`extra_choices` updated. Sets referenced by fields but absent from both the DB and
`choice_sets` still raise `UnknownChoiceSetError`.

### Object instances (optional)

Documents may include a top-level `objects` array so that apply can upsert Custom Object
**instances** after types (and choice sets) are written. Export does not include this
section.

```json
{
  "schema_version": "1",
  "choice_sets": [ ... ],
  "types": [ ... ],
  "objects": [
    {
      "type": "security-action",
      "records": [
        {"name": "Permit", "status": "active", "color": "#28a745"},
        {"name": "Deny", "status": "active", "color": "#dc3545"}
      ]
    },
    {
      "type": "security-zone",
      "records": [
        {"name": "trust", "status": "active", "color": "#28a745"},
        {"name": "untrust", "status": "active", "color": "#dc3545"}
      ]
    },
    {
      "type": "security-rb-demo1",
      "records": [
        {
          "index": 1,
          "status": true,
          "name": "trust-to-untrust-https",
          "source": ["security-zone/trust"],
          "destination": ["security-zone/untrust"],
          "services_applications": ["security-service/HTTPS"],
          "actions": ["security-action/Permit"]
        }
      ]
    }
  ]
}
```

Each group requires `type` (COT slug) and `records` (non-empty list). Each record is a
map of field names to values. The COT **primary field** must be present in every record;
rows are matched with `update_or_create` on that value.

**Scalar fields:** text, longtext, integer, decimal, boolean, date, datetime, url, json,
select, multiselect.

**Custom object references:** `object` and `multiobject` columns accept
`"cot-slug/primary-value"` strings (or lists thereof for `multiobject`). The slug is the
target COT slug; the value after `/` is the target row's primary field value (e.g.
integer primary `index: 1` → `"security-rb-demo1/1"`). Referenced groups must appear
**earlier** in the `objects` array. Alternative dict form:
`{"ref": "security-zone/trust"}`.

Built-in NetBox object references (e.g. `ipam/prefix`) are **not** supported in `objects`.

Apply order: `choice_sets` → `types` → `objects` (single transaction).

See also: `netbox_custom_objects/schema/examples/security_objects.json` for a full
security demo (zones, actions, services, rulebook rules).

### Related Object Type Encoding

`related_object_type` (and each entry in `related_object_types`) is encoded as a `/`-separated string:

- **Built-in NetBox model:** `"dcim/device"`, `"ipam/prefix"`
- **Custom Object Type:** `"custom-objects/<cot-slug>"`, e.g. `"custom-objects/circuit"`

### Tombstone Record

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

### Exporting a Schema

#### Web UI

Three export paths produce the same portable-schema JSON document:

**Custom Object Type detail — Export tab**

Open any Custom Object Type and select the **Export** tab
(`/plugins/custom-objects/custom-object-types/<id>/export/`). The page shows a
read-only JSON document for that type with a **Copy** button.

**Custom Object Types list — export dropdown**

On the Custom Object Types list, open the export dropdown and choose **Portable
schema (JSON)**. The download respects the current table filter and saves a
`custom-object-types-schema.json` file.

#### REST API

`GET /api/plugins/custom-objects/schema/export/`

Returns a portable schema document for all Custom Object Types, or restrict with
repeated ``slug`` query parameters:

```http
GET /api/plugins/custom-objects/schema/export/?slug=circuit&slug=device-profile
Authorization: Token <token>
Accept: application/json
```

Response body (types only — no `choice_sets` or `objects`; add those when preparing imports):

```json
{
  "schema_version": "1",
  "types": [ ... ]
}
```

#### Python API

Use the Python API from `netbox_custom_objects.schema.exporter`. Before running either
option, switch to your NetBox installation root and activate its virtualenv:

```bash
# Replace /opt/netbox with your NetBox installation root.
export NETBOX_ROOT=/opt/netbox
cd "$NETBOX_ROOT"
source "$NETBOX_ROOT/venv/bin/activate"
```

#### Option 1 — NetBox shell (recommended)

Run `manage.py nbshell` from the NetBox installation root. Django is fully initialised for
you, and all models are importable immediately:

```bash
python3 netbox/manage.py nbshell
```

Then inside the shell, export specific COTs by slug:

```python
from netbox_custom_objects.schema.exporter import export_cots
from netbox_custom_objects.models import CustomObjectType
import json

cots = CustomObjectType.objects.filter(slug__in=["circuit", "device-profile"])
document = export_cots(cots)
print(json.dumps(document, indent=2))
```

Or export **all** COTs at once:

```python
from netbox_custom_objects.schema.exporter import export_cots
from netbox_custom_objects.models import CustomObjectType
import json

cots = CustomObjectType.objects.all()
document = export_cots(cots)
print(json.dumps(document, indent=2))
```

To run a script file non-interactively, pipe it in:

```bash
python3 netbox/manage.py nbshell < /path/to/export_cot.py
```

#### Option 2 — Standalone script

If you need to run a `.py` file directly (e.g. from a cron job or CI pipeline), you must
bootstrap Django yourself **before** importing any NetBox or plugin code:

```bash
PYTHONPATH="$NETBOX_ROOT/netbox" python3 /path/to/export_cot.py
```

Where `/path/to/export_cot.py` is your script, containing:

```python
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")
django.setup()  # must be called before any model or app imports

from netbox_custom_objects.schema.exporter import export_cots
from netbox_custom_objects.models import CustomObjectType
import json

cots = CustomObjectType.objects.filter(slug__in=["circuit", "device-profile"])
document = export_cots(cots)
print(json.dumps(document, indent=2))
```

!!! warning "Missing `django.setup()` causes `AppRegistryNotReady`"
    Setting `DJANGO_SETTINGS_MODULE` alone is not sufficient — Django also needs
    `django.setup()` to populate its app registry. Without it you will see:
    `AppRegistryNotReady: Apps aren't loaded yet.`

---

`export_cots` returns a dict with `schema_version` and `types`. For a single COT without the
document wrapper, use `export_cot(cot)`.

> **Fields without a `schema_id`** (created before the portable schema feature was introduced)
> are skipped with a `WARNING` log entry. Run the backfill migration (see below) to assign IDs
> to pre-existing fields.

### Previewing a Schema (API)

`POST /api/plugins/custom-objects/schema/preview/`

Submit a schema document and receive a structured diff showing what would change, **without
modifying the database**. Optional `choice_sets` and `objects` keys are validated against
the JSON Schema but do not affect the diff (preview is type-centric only).

```http
POST /api/plugins/custom-objects/schema/preview/
Content-Type: application/json
Authorization: Token <token>

{
  "schema_version": "1",
  "choice_sets": [
    {"name": "status_choices", "choices": ["active", "reserved"]}
  ],
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

### Applying a Schema

#### Web UI

Import (and preview) portable-schema documents from the NetBox UI:

1. Go to **Custom Objects → Custom Object Types → Add**.
2. Open the **JSON** tab on
   `/plugins/custom-objects/custom-object-types/add/`.
3. Paste a JSON schema document (JSON only — YAML is not accepted).
4. Click **Preview** to validate the document against `cot_schema_v1.json` and
   see the comparator diff. No database changes are made.
5. Click **Apply** to create or update Custom Object Types via the schema
   executor. Requires both *add* and *change* permissions on Custom Object Types.

Enable **Allow destructive changes** when the diff includes field removals that
drop DB columns. The **Create** tab on the same page remains available for
adding a single Custom Object Type through the standard form. CSV bulk import
is available separately via **Import** on the list page.

When a [NetBox Branching](branching.md) branch is active, the UI shows a
warning. Schema DDL is routed to the active branch's PostgreSQL schema; see
[Portable Schema and Branching](branching.md#portable-schema) for details.

#### REST API

`POST /api/plugins/custom-objects/schema/apply/`

```http
POST /api/plugins/custom-objects/schema/apply/
Content-Type: application/json
Authorization: Token <token>

{
  "allow_destructive": false,
  "schema": {
    "schema_version": "1",
    "choice_sets": [
      {"name": "status_choices", "choices": ["active", "reserved"]}
    ],
    "types": [ ... ],
    "objects": [
      {
        "type": "security-action",
        "records": [
          {"name": "Permit", "status": "active", "color": "#28a745"}
        ]
      }
    ]
  }
}
```

- **`allow_destructive`** (default `false`): must be `true` for the apply to proceed when the
  diff contains `REMOVE` operations. If `false` and removals are present, the endpoint returns
  `409 Conflict`.
- **`choice_sets`**: optional; `CustomFieldChoiceSet` rows are created or updated before types.
- **`objects`**: optional; instances are upserted after types (see [Object instances](#object-instances-optional)).
- The apply is **fully atomic** — a failure at any point rolls back all changes including newly
  created COT tables, choice sets, and seeded instances (PostgreSQL supports transactional DDL).
- On success, `schema_document` is persisted on each affected COT so tombstones are available
  for future export/diff cycles.
- When a branch is active, schema DDL targets the branch's PostgreSQL schema (not main).
  Custom Object Type metadata remains exempt from branch tracking — see
  [Portable Schema and Branching](branching.md#portable-schema).

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

Response `400 Bad Request` (invalid JSON Schema — validation before apply):

```json
{
  "schema_errors": [
    {"path": ["types", 0, "fields"], "message": "..."}
  ]
}
```

Response `400 Bad Request` (circular COT dependency among new types):

```json
{
  "error": "circular_dependency",
  "detail": "..."
}
```

Response `400 Bad Request` (missing choice set, unknown field type, or missing related type
while applying **types**):

```json
{
  "error": "unresolvable_reference",
  "detail": "..."
}
```

Response `400 Bad Request` (object seed failure — bad reference, missing primary, wrong order
in **`objects`**):

```json
{
  "error": "object_seed",
  "detail": "No security-zone object with name=trust; seed referenced objects before dependents."
}
```


### Deleting Custom Object Types

Deletion is guarded at the **Custom Object Type (schema) level**. This is separate
from `on_delete_behavior` on single `object` fields, which controls what happens
when a **referenced object instance** (core NetBox or Custom Object) is deleted.
`on_delete_behavior` does not apply to `multiobject` fields.

A Custom Object Type cannot be deleted while:

1. **Instances still exist** — delete or migrate all objects of that type first.
   The list view **Objects** column links to the object list when the count is
   greater than zero. If the dynamic backing table is already missing (orphaned
   schema metadata), the count reads as zero and this check alone will not block
   deletion.
2. **Another type's schema still references it** — for example, `security-rb-demo1`
   defines `source`, `destination`, and `actions` fields that point at other security
   types. Those referenced types (such as `security-action` or `security-zone`) cannot
   be removed until `security-rb-demo1` is deleted first. The list view **Referenced
   by** column shows how many other types still depend on this one (hover for names).

These checks apply to single-type delete, bulk delete from the Custom Object Types
list, and the REST API. The UI and API return a blocking error instead of silently
breaking cross-type references. Delete dependent types before the types they reference.

If the dynamic backing table is already absent (for example after a partial failure
or manual `DROP TABLE`), delete still completes so orphaned COT metadata can be removed
without a database error.

### Typical End-to-End Workflow

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

## Field Deprecation Lifecycle

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

## Comparator (Developer Reference)

`netbox_custom_objects/schema/comparator.py` — pure-read, no DB writes.

```python
from netbox_custom_objects.schema.comparator import diff_document, diff_cot

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

### Matching Rules

- Fields are matched exclusively by `schema_id`. DB fields with no `schema_id` generate a
  **warning**, not a `REMOVE`.
- `REMOVE` is emitted only when a `schema_id` appears in the document's `removed_fields`.
  A field absent from both `fields` and `removed_fields` generates a **warning**.

---

## Executor (Developer Reference)

`netbox_custom_objects/schema/executor.py` — writes to the DB.

```python
from netbox_custom_objects.schema.executor import apply_document, apply_diffs

diffs = apply_document(schema_doc, allow_destructive=False)  # list[COTDiff]
apply_diffs(diffs, type_defs_by_slug, allow_destructive=False)  # lower-level
```

`apply_document` is the primary entry point. It runs, in order: `ensure_choice_sets()`,
`apply_diffs()`, `ensure_objects()`. `apply_diffs` is available when diffs have been
pre-computed by the comparator (e.g. for preview-then-apply flows).

All DB writes are wrapped in a single `transaction.atomic()` block. Any exception causes a
full rollback.

### Exceptions

| Exception | Raised when |
|-----------|-------------|
| `DestructiveChangesError` | `REMOVE` operations are present and `allow_destructive=False` |
| `CircularDependencyError` | Cross-COT `related_object_type` references form a cycle among new COTs |
| `UnknownChoiceSetError` | A `choice_set` name cannot be resolved during **type** apply |
| `UnknownObjectTypeError` | A `related_object_type` string cannot be resolved, or an `objects` group references an unknown COT slug |
| `UnknownFieldTypeError` | A `type` value is not one of the supported field type strings |
| `ObjectSeedError` | An `objects` record is invalid, references a missing row, or uses an unsupported field value |

REST apply maps `ObjectSeedError` to HTTP 400 with `"error": "object_seed"`; the other
reference errors (except `DestructiveChangesError` / `CircularDependencyError`) map to
`"error": "unresolvable_reference"`.

`DestructiveChangesError` is raised **before** the transaction opens, so the DB is never
touched. The other exceptions may be raised mid-transaction, triggering a full rollback.

### Dependency Ordering

When a schema document contains multiple new COTs that reference each other via
`related_object_type: "custom-objects/<slug>"`, the executor performs a topological sort to
ensure referenced COT tables exist before any referencing field is added. Cycles among new
COTs raise `CircularDependencyError`.

---

## Backfilling Pre-Existing Fields

Fields created before the portable schema feature was introduced have `schema_id = null`.
Migration `0008_backfill_schema_ids` assigns IDs to all such fields in PK order and updates
each COT's `next_schema_id` counter accordingly. This migration runs automatically with
`manage.py migrate`.

After the backfill, all existing fields participate in export and diff cycles normally.

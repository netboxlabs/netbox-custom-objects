# Portable schema examples

Reference documents for the COT portable schema format (`schema_version: "1"`).
Recommended document order: `schema_version` → `choice_sets` → `types` → `objects`.
These files are **not** loaded automatically — they are not wired into the NetBox
UI, bundle loader, or navigation.

Apply manually via the schema API (`POST /api/plugins/custom-objects/schema/apply/`)
or from a script:

```python
import json
from netbox_custom_objects.schema.executor import apply_document

with open("security_objects.json") as f:
    doc = json.load(f)
apply_document(doc, allow_destructive=False)
```

When a document includes a top-level `choice_sets` array, `apply_document` creates
or updates the referenced `CustomFieldChoiceSet` rows before applying COT types.

When a document includes a top-level `objects` array, instances are upserted by
primary field **after** types are applied (scalar fields only).

| File | Description |
|------|-------------|
| `security_objects.json` | Security policy types, choice sets, demo zones (trust/untrust), actions, services, and four rulebook rules. |

See also: [`docs/portable-schema.md`](../../../../docs/portable-schema.md).

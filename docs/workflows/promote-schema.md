# Promoting a Schema Between Instances

!!! warning "Draft"
    The steps on this page haven't been verified yet.

**Scenario:** you design and test Custom Object Types on a staging NetBox, and want to create the same types on production without rebuilding them by hand. Later, you change a type on staging and roll the change out too.

**You'll use:** [portable schema](../portable-schema.md) export, preview and apply, schema versions, and tombstones for removed fields.

## Before You Start

- Staging has the Support Contract type from [A self-contained object type](self-contained-type.md).
- You can run `manage.py nbshell` on staging.
- On production, you have an API token with write access, for a user who can add and change Custom Object Types and their fields.
- Production has a custom field choice set with the same name, `Contract status`, and the same choices. Schema documents refer to choice sets by name but don't include them.

## Steps

### 1. Set a Version on Staging

On staging, edit the Support Contract type, set **Version** to `1.0.0`, and save. The version travels with the schema document, so you can tell which revision an instance has.

### 2. Export the Schema

1. On staging, from the NetBox installation root, start the NetBox shell:

    ```no-highlight
    python3 netbox/manage.py nbshell
    ```

2. Write the type's schema document, and an apply request wrapping it, to files. The NetBox shell is interactive, so keep the blank line after each `with` block:

    ```python
    import yaml
    from netbox_custom_objects.models import CustomObjectType
    from netbox_custom_objects.schema.exporter import export_cots

    document = export_cots(CustomObjectType.objects.filter(slug='support-contracts'))
    with open('/tmp/contracts-schema.yaml', 'w') as f:
        yaml.safe_dump(document, f, sort_keys=False)

    with open('/tmp/contracts-apply.yaml', 'w') as f:
        yaml.safe_dump({'allow_destructive': False, 'schema': document}, f, sort_keys=False)

    ```

    The same lines can be saved to a file and piped in with `python3 netbox/manage.py nbshell < export.py`.

3. Copy the files somewhere safe. Keeping `contracts-schema.yaml` in version control lets you review each change as a diff.

### 3. Preview on Production

Send the document to production's preview endpoint. Nothing is changed:

```no-highlight
curl -X POST \
     -H "Authorization: Bearer nbt_<key>.<token>" \
     -H "Content-Type: application/yaml" \
     -H "Accept: application/yaml" \
     --data-binary @/tmp/contracts-schema.yaml \
     https://netbox.example.com/api/plugins/custom-objects/schema/preview/
```

The response shows the Support Contract type as new (`is_new: true`), with an `add` for each field.

### 4. Apply on Production

```no-highlight
curl -X POST \
     -H "Authorization: Bearer nbt_<key>.<token>" \
     -H "Content-Type: application/yaml" \
     -H "Accept: application/yaml" \
     --data-binary @/tmp/contracts-apply.yaml \
     https://netbox.example.com/api/plugins/custom-objects/schema/apply/
```

The response says `applied: true`. The Support Contract type now exists on production. Set its **Display expression** there by hand if you used one: display expressions aren't part of the schema document yet.

### 5. Change the Type on Staging

1. On staging, add an Integer field `renewal_notice_days` (label `Renewal notice (days)`, display weight `145`), delete the **Notes** field, and set the type's **Version** to `1.1.0`.
2. Export again, as in step 2.
3. Deleting a field in the UI doesn't record its removal in the schema document, so add it by hand. In both files, under the type, add a tombstone with the deleted field's `id` from the version 1.0.0 export:

    ```yaml
    removed_fields:
      - {id: 7, name: notes, type: longtext, removed_in: "1.1.0"}
    ```

    Without it, production keeps the field, and the preview only warns that the field "exists in the DB but is absent" from the document.

### 6. Roll Out the Change

1. Preview the new document on production. It shows an `add` for the new field and a `remove` for **Notes**, with `has_destructive_changes: true`.
2. Apply it. Because it removes a field, the apply is refused with `409 Conflict` (`error: destructive_changes`) and nothing changes.
3. Once you've confirmed that dropping the Notes column is intended, set `allow_destructive: true` in `contracts-apply.yaml` and apply again. The response says `applied: true`, and production's copy of the type records the removed field, so later exports from production keep its tombstone.

### 7. Try It in a Branch First (Optional)

If production uses [NetBox Branching](../branching.md), add `-H "X-NetBox-Branch: <branch schema ID>"` to the apply request to make the changes in a branch, review them there, and merge the branch.

## Result

Production has the same Support Contract type as staging, at version 1.1.0, and you have a repeatable way to roll out future changes.

## See Also

- [Portable Schema](../portable-schema.md) for the document format and every error response.
- [Upgrading](../upgrading.md) for upgrading the plugin itself, which is separate from changing your types.

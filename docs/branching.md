# Using NetBox Custom Objects with NetBox Branching

Custom Objects fully supports [NetBox Branching](https://netboxlabs.com/docs/extensions/branching/). Custom Object Types, their fields and constraints, and the custom objects themselves can all be created, changed and deleted in a branch, then synced, merged and reverted like any other NetBox object.

!!! tip
    If you have any questions, the best place to start is the GitHub [discussions](https://github.com/netboxlabs/netbox-custom-objects/discussions). If you are a NetBox Labs customer, you can also contact support.

## Requirements

Using Custom Objects together with NetBox Branching requires:

- NetBox 4.6.2 or later
- netbox-branching 1.0.4 or later (published on PyPI as `netboxlabs-netbox-branching`)

These requirements apply only when `netbox_branching` is in `PLUGINS`. Without it, the standard [compatibility matrix](https://github.com/netboxlabs/netbox-custom-objects/blob/main/COMPATIBILITY.md) applies.

NetBox runs these system checks at startup:

| Check | Level | Meaning |
|-------|-------|---------|
| `netbox_custom_objects.E001` | Error | NetBox is older than 4.6.2. |
| `netbox_custom_objects.E002` | Error | netbox-branching is older than 1.0.4. |
| `netbox_custom_objects.W001` | Warning | netbox-branching is installed, but its version can't be determined (for example, an editable checkout without package metadata), so the 1.0.4 requirement can't be verified. |

## Setup

1. Install and configure netbox-branching as described in its [installation guide](https://netboxlabs.com/docs/extensions/branching/), including its `DATABASES` and `DATABASE_ROUTERS` settings.

    You can install both plugins at compatible versions with the `branching` extra:

    ```no-highlight
    pip install "netboxlabs-netbox-custom-objects[branching]"
    ```

2. Add both plugins to `PLUGINS` in `configuration.py`:

    ```python
    PLUGINS = [
        'netbox_custom_objects',
        'netbox_branching',
    ]
    ```

Custom Objects needs no branching-specific configuration.

!!! warning
    Don't add `netbox_custom_objects.customobjecttype` or `netbox_custom_objects.customobjecttypefield` to netbox-branching's `exempt_models` setting. Earlier versions of these docs recommended it, but it now stops branches from tracking changes to Custom Object Types and their fields. If your configuration has it, remove it.

## What Works in a Branch

**Custom Object Types, fields and constraints.** Creating, editing and deleting a Custom Object Type, its fields or its constraints in a branch changes only the branch. That includes renaming a field, changing its type, and toggling Required or Must be unique. The database table changes run against the branch's schema, so main is unchanged until the branch is merged.

**Custom objects.** Creating, editing and deleting custom objects works the same way, for every field type. That includes object and multi-object fields that point at another Custom Object Type, polymorphic fields, and fields that point at their own type. A new Custom Object Type and the objects that reference it can be created together in the same branch.

**Tags, journal entries and choice sets.** Tags and journal entries on custom objects, and edits to a choice set used by a select field, carry over when the branch is merged.

**Portable schema.** A [portable schema document](portable-schema.md) can be applied in a branch. Over the REST API, select the branch with the `X-NetBox-Branch` header. If the apply fails or is refused, the branch is left unchanged.

**GraphQL.** The GraphQL API follows the active branch, so its custom object types and fields match the branch's Custom Object Types.

## Sync, Merge and Revert

Changes made in a branch appear in its diff and in the Changes Ahead view, and behave like other NetBox objects:

- **Sync** brings new Custom Object Types, fields and objects from main into the branch. Where main and the branch changed the same value, main's value wins.
- **Merge** applies the branch's changes to main, using either the iterative or the squash merge strategy. Where both sides changed the same value, the branch's value wins. Likewise, if a field was renamed in both main and the branch, the branch's name wins.
- **Revert** undoes a merged branch, including schema changes such as added, renamed or deleted fields.

Deleting a branch without merging it leaves main untouched. Deleting a Custom Object Type in a branch doesn't affect main's copy of it.

## Upgrades

When an upgrade adds columns to Custom Object Type tables, `manage.py migrate` updates every active branch as well as main. Running `manage.py upgrade_custom_objects` with no `--cot` option does the same. Branches that are missing their schema or have migrations pending are skipped, and you can run the command again once those branches are migrated. With `--cot`, only main is updated.

## Known Limitations

- **Reverting a deleted Custom Object Type doesn't restore its objects.** If a Custom Object Type is deleted in a branch and the branch is merged and then reverted, the type, its fields and its table come back with their original IDs, but its objects don't. Deleting the type drops its table directly, so there is no change record for each object to replay.
- **Reverse relations for polymorphic fields.** For a polymorphic field added in a branch, its reverse relation on the target models (the field's **Related name**) isn't available until the branch is merged.

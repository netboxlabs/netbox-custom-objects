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
    If your configuration adds the Custom Object Type models to netbox-branching's `exempt_models`, as earlier versions of these docs recommended, remove them.

## How It Works

Custom Object Types, their fields and constraints, and custom objects work in branches like any other NetBox object: create, change and delete them in a branch, then sync, merge and revert it as described in the [netbox-branching documentation](https://netboxlabs.com/docs/extensions/branching/). What's specific to Custom Objects:

- **Schema changes stay in the branch.** Creating or deleting a Custom Object Type, and adding, renaming, retyping or deleting a field, change the database tables in the branch's own schema. Main's tables are unchanged until the branch is merged.
- **Field renames.** If a field is renamed in both main and the branch, merging keeps the branch's name.
- **Portable schema.** A [portable schema document](portable-schema.md) can be applied in a branch. Over the REST API, select the branch with the `X-NetBox-Branch` header.
- **GraphQL.** The GraphQL API follows the active branch, so its custom object types and fields match the branch's Custom Object Types.

## Upgrades

When an upgrade adds columns to Custom Object Type tables, `manage.py migrate` updates every active branch as well as main. Running `manage.py upgrade_custom_objects` with no `--cot` option does the same. Branches that are missing their schema or have migrations pending are skipped, and you can run the command again once those branches are migrated. With `--cot`, only main is updated.

## Known Limitations

- **Reverting a deleted Custom Object Type doesn't restore its objects.** If a Custom Object Type is deleted in a branch and the branch is merged and then reverted, the type, its fields and its table come back with their original IDs, but its objects don't. Deleting the type drops its table directly, so there is no change record for each object to replay.
- **Reverse relations for polymorphic fields.** For a polymorphic field added in a branch, its reverse relation on the target models (the field's **Related name**) isn't available until the branch is merged.

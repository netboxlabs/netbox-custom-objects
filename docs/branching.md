# Using NetBox Custom Objects with NetBox Branching

When using Custom Objects together with NetBox Branching, the following minimum versions are required:

- NetBox >= 4.6.2
- netbox-branching >= 1.0.4

These requirements are only enforced when `netbox_branching` is present in `PLUGINS`. If you do not use branching, the standard compatibility matrix in `COMPATIBILITY.md` applies. A Django system check (`netbox_custom_objects.E001` / `E002`) will fail at startup if the combination is misconfigured.

As of version 0.4.0, Custom Objects is _compatible_ with [NetBox Branching](https://netboxlabs.com/docs/extensions/branching/), but not yet fully supported. Users can safely run both plugins together, but there are some caveats to be aware of. See below for how each Custom Objects model interacts with NetBox Branching.

!!! note
    We are working towards full support for Custom Objects on branches. Keep an eye on the GitHub issues for updates ahead of future releases.

!!! tip
    If you have any questions, the best place to start is the GitHub [discussions](https://github.com/netboxlabs/netbox-custom-objects/discussions). If you are a NetBox Labs customer, you can also contact support.

## Setup

To use Custom Objects alongside NetBox Branching, add the following to your `configuration.py`. This exempts Custom Object Types and their fields from branch tracking, which is the supported operating mode:

```python
PLUGINS_CONFIG = {
    'netbox_branching': {
        'exempt_models': [
            'netbox_custom_objects.customobjecttype',
            'netbox_custom_objects.customobjecttypefield',
        ],
    },
}
```

## Custom Object Types & Fields

Custom Object Types and Custom Object Type Fields can be created, updated, and deleted while a branch is active. However, those changes are applied directly to the main database — they do not appear in the branch diff or the "Changes Ahead" view.

- When you are in an activated branch, any creates, updates, and deletes you perform on Custom Object Types and Custom Object Type Fields will not show up in the Diff or Changes Ahead views.
- Although you are in an activated branch, these changes are made directly to main.
- Typically it will be NetBox administrators who alter Custom Object Types and Custom Object Type Fields. We recommend experimenting in a staging instance until you are satisfied with the modelling, and then moving your schemas to production using the [portable schema](portable-schema.md) feature.

## Custom Objects

Changes to Custom Objects on branches are disallowed.

- When in an activated branch, users can still see the available Custom Object Types and any Custom Objects that existed when the branch was created, but cannot create, edit, or delete Custom Objects within the branch.
- This approach ensures users can safely combine Custom Objects and Branching while full support is being developed.

## Portable Schema

Applying a [portable schema document](portable-schema.md) is also blocked while a branch is active. The schema executor performs direct DDL (`ALTER`/`DROP TABLE`) operations that are not branch-aware, so schema applies must be run from the main context.

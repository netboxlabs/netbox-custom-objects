# Custom Object Type Constraints

A Custom Object Type Constraint requires a combination of a Custom Object Type's field values to be unique across its objects, for example a serial number that only has to be unique per vendor. The database enforces it, so it applies however objects are created or changed. See the [Custom Objects documentation](https://github.com/netboxlabs/netbox-custom-objects/blob/main/docs/index.md#adding-constraints-to-the-custom-object-type) for details.

## Fields

### Name

A lowercased, URL-friendly name, unique within the Custom Object Type, e.g. `vendor_serial`.

### Type

The kind of constraint. Only `Unique` is currently supported.

### Fields

The fields whose combined values must be unique. Choose at least two, or one when the constraint is case-insensitive. Multi-object, multi-select, JSON and coordinates fields can't be included.

### Case-Insensitive

Compare text, long text and URL fields without regard to case.

### Empty Values Are Distinct

When enabled (the default), objects that leave any of the constrained fields empty never conflict. Disable it to treat empty values as equal.

### Description

A short, optional description of the constraint.

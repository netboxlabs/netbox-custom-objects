# Custom Object Type Constraints

A Custom Object Type Constraint either requires a combination of a Custom Object Type's field values to be unique across its objects, for example a serial number that only has to be unique per vendor, or requires every object to satisfy a rule, for example an end date after its start date. The database enforces it, so it applies however objects are created or changed, and it can't be saved while existing objects already break it. See the [Custom Objects documentation](https://netboxlabs.com/docs/extensions/custom-objects/#adding-constraints-to-the-custom-object-type) for details.

## Fields

### Name

A lowercased, URL-friendly name of up to 50 characters, unique within the Custom Object Type, e.g. `vendor_serial`. Names may not start or end with an underscore, and double underscores are not allowed.

### Type

The kind of constraint: `Unique` (the default) or `Check`.

### Fields

For a unique constraint: the fields whose combined values must be unique. Choose at least two, or one when the constraint is case-insensitive or has a condition. Multi-object, multi-select, JSON, coordinates and array fields can't be included. A type can't have two unique constraints with the same fields and settings.

### Case-Insensitive

For a unique constraint: compare text, long text and URL fields without regard to case. The constraint must include at least one such field.

### Empty Values Are Distinct

For a unique constraint: when enabled (the default), objects that leave any of the constrained fields empty never conflict. Disable it to treat empty values as equal; this requires PostgreSQL 15 or later.

### Condition

Optionally, apply the constraint only to objects that match all of a list of predicates, e.g. `[{"field": "parent", "lookup": "isnull", "value": true}]`. Each predicate names a field and a lookup: `exact`, `n` (does not equal, or is empty), `in` (a list of values), `isnull` (`true` for empty, `false` for not empty), or `gt`, `gte`, `lt` and `lte` for integer, decimal, date and datetime fields. A value of `{"field": "other_field"}` compares with another field of the same kind. Object fields can only be tested with `isnull`.

### Rule

For a check constraint: predicates, in the same form as the condition, that every object (matching the condition, if any) must satisfy, e.g. `[{"field": "end_date", "lookup": "gt", "value": {"field": "start_date"}}]`. A comparison passes when a value it compares is empty; add an `isnull` predicate with `false` to require a value.

### Error Message

For a check constraint: the error shown when an object breaks the rule. If left blank, the error describes the rule.

### Description

A short, optional description of the constraint.

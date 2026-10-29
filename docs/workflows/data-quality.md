# Keeping Data Clean

!!! warning "Draft"
    The steps on this page haven't been verified yet.

**Scenario:** your support contracts need some rules:
- contract numbers follow your format;
- a contract number is unique per vendor (different vendors may reuse a number);
- an end date is never before its start date;
- active contracts must have an end date.

**You'll use:** validation regexes, required fields, unique and check constraints, and NetBox's `CUSTOM_VALIDATORS` setting.

## Before You Start

- The Support Contract type from [A self-contained object type](self-contained-type.md) exists, with a few contracts.
- Your user can change Custom Object Type fields and add constraints. Step 5 needs access to NetBox's `configuration.py`.

## Steps

### 1. Check the Contract Number Format

1. On the Support Contract type's page, open the **Fields** tab and edit **Contract number**.
2. Set **Validation regex** to `^[A-Z]+-[0-9]+$` and save.
3. Try saving a contract with the number `acme 1001`. The form shows "Values must match this regex" under **Contract number**.

Existing values aren't rechecked when you add a regex; it applies the next time each contract is saved.

### 2. Make Contract Numbers Unique per Vendor

1. On the type's page, click **Add** and choose **Constraint**.
2. Set **Name** to `unique_number_per_vendor` and **Type** to **Unique**.
3. Under **Fields**, choose **Vendor** and **Contract number**, and enable **Case-insensitive**, so `Acme` and `ACME` count as the same vendor.
4. Click **Create**. If existing contracts already break the rule, NetBox says so ("Existing Support Contract objects already have duplicate values for these fields.") and doesn't create the constraint.
5. Try adding a contract with vendor `ACME` and the number of an existing Acme contract. It's rejected with "Support Contract with this Contract number and Vendor already exists." The same number with vendor `Globex` is accepted.

### 3. Require the End Date After the Start Date

1. Add another constraint with **Name** `end_after_start` and **Type** **Check**.
2. Set **Rule** to:

    ```json
    [{"field": "end_date", "lookup": "gt", "value": {"field": "start_date"}}]
    ```

3. Set **Error message** to `The end date must be after the start date.` and click **Create**. The **Constraints** tab describes the rule as "End date is greater than Start date".
4. Try saving a contract that ends before it starts. Your error message is shown. A contract with no end date is still accepted: a comparison with an empty value passes.

### 4. Require an End Date on Active Contracts

1. Add a check constraint named `active_needs_end_date`.
2. Set **Condition** to `[{"field": "status", "lookup": "exact", "value": "active"}]`, so the rule only applies to active contracts.
3. Set **Rule** to `[{"field": "end_date", "lookup": "isnull", "value": false}]`.
4. Set **Error message** to `Active contracts need an end date.` and click **Create**.
5. Try saving an active contract with no end date, then a pending one. Only the active one is rejected.

### 5. Add a Rule with a Custom Validator (Optional)

Constraints cover comparisons between fields. For other rules, NetBox's [`CUSTOM_VALIDATORS`](https://netboxlabs.com/docs/netbox/configuration/data-validation/#custom_validators) setting applies to custom objects too, keyed by the type's slug:

1. In `configuration.py`, add (the key ends with the type's slug):

    ```python
    CUSTOM_VALIDATORS = {
        'netbox_custom_objects.support-contracts': [
            {'vendor': {'min_length': 2}},
        ],
    }
    ```

2. Restart NetBox.
3. Try saving a contract with a one-letter vendor. It's rejected with "Custom validation failed for vendor", followed by the validator's message.

### 6. Check That Every Path Is Covered

Repeat one rejected change through the REST API and through a CSV import. The same rules apply: the REST API returns `400 Bad Request` with the same message, and the import is rejected without creating anything.

## Result

Invalid contracts are rejected in the UI, the REST API and imports.

## See Also

- [Constraints](../index.md#adding-constraints-to-the-custom-object-type) for every lookup and option.
- [Field Attributes](../field-attributes.md) for Required, Must be unique and validation settings.

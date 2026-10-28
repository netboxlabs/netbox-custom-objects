# A Self-Contained Object Type

!!! warning "Draft"
    The steps on this page haven't been verified yet.

**Scenario:** you want to track support contracts in NetBox: who each one is with, its contract number, when it starts and ends, its status, and a link to the vendor's portal. Contracts don't need to reference anything else in NetBox yet.

**You'll use:** Custom Object Types, fields of several types, a choice set, the display name, list and detail views, and search.

## Before You Start

- Custom Objects is [installed](../installation.md).
- Your user can add Custom Object Types and fields, and custom field choice sets (or is a superuser). See [Permissions](../permissions.md).

## Steps

### 1. Create a Choice Set for the Status

1. Go to **Customization > Custom Field Choices** and click **Add**.
2. Set **Name** to `Contract status`.
3. In **Extra choices**, enter one choice per line:

    ```no-highlight
    active:Active
    pending:Pending
    expired:Expired
    ```

4. Click **Create**.

### 2. Create the Custom Object Type

1. Go to **Custom Objects > Custom Object Types** and click **Add**.
2. Fill in:

    | Field | Value |
    |-------|-------|
    | Internal name | `support_contract` |
    | Display name (singular) | `Support Contract` |
    | Display name (plural) | `Support Contracts` |
    | URL path/slug | `support-contracts` (filled in from the plural name) |
    | Description | `Vendor support contracts` |

3. Click **Create**. NetBox shows the new type's page.

### 3. Add the Fields

For each row below, click **Add** on the type's page, choose **Field**, fill in the values, and click **Create**:

| Name | Label | Type | Other settings |
|------|-------|------|----------------|
| `contract_number` | Contract number | Text | **Required**, **Primary name field** |
| `vendor` | Vendor | Text | **Required** |
| `status` | Status | Selection | **Choice set:** Contract status; **Default:** `"active"` |
| `start_date` | Start date | Date | |
| `end_date` | End date | Date | |
| `portal` | Support portal | URL | |
| `notes` | Notes | Text (long) | |

### 4. Set the Display Name (Optional)

By default, each contract is named after its contract number, the primary name field. To include the vendor:

1. On the type's page, click **Edit**.
2. Set **Display expression** to `{{ vendor }} {{ contract_number }}`.
3. Click **Save**.

### 5. Add Some Contracts

1. In the navigation menu, under **Custom Objects > Objects**, click the **+** next to **Support Contracts**.
2. Fill in a contract, e.g. vendor `Acme`, contract number `ACME-1001`, start and end dates a year apart, and the vendor's portal URL.
3. Click **Create**, then add two or three more.

### 6. Find and Review Them

1. Click **Support Contracts** in the navigation menu to open the list view. Use **Configure Table** to show the vendor, status and end date columns.
2. Filter the list, e.g. by status.
3. Search for `ACME-1001` in NetBox's global search. The contract appears in the results.
4. Open a contract and check its **Changelog** and **Journal** tabs.

## Result

You have a Support Contract type with its own menu entry, list and detail views, REST API endpoint and search results, and a few contracts in it.

## See Also

- [Field Attributes](../field-attributes.md) for every field type and setting.
- [Bulk importing and editing](bulk-import.md) to load many contracts at once.
- [Keeping data clean](data-quality.md) to add validation rules to this type.

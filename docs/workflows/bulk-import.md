# Bulk Importing and Editing

**Scenario:** you have a spreadsheet of existing support contracts to load into NetBox, and later need to change many of them at once. You also want to load static routes that reference devices by name.

**You'll use:** bulk import, bulk edit, export, and updating objects by re-importing them.

## Before You Start

- The Support Contract type from [A self-contained object type](self-contained-type.md) exists.
- For step 5, the Static Route type from [Linking to NetBox objects](link-netbox-objects.md) exists, along with the devices you'll reference.
- Your user can add and change Support Contracts and Static Routes.

## Steps

### 1. Check the Import Format

1. Click **Support Contracts** in the navigation menu, then **Import**.
2. Scroll down to **Field Options**. It lists each field's column name, with a check mark for required ones. Click the **?** next to **Status** to see its accepted values.

### 2. Import Contracts

1. On the **Direct Import** tab, paste CSV data whose first row names the fields:

    ```csv
    contract_number,vendor,status,start_date,end_date,portal
    ACME-2001,Acme,active,2026-01-01,2026-12-31,https://support.acme.example
    ACME-2002,Acme,pending,2026-07-01,2027-06-30,https://support.acme.example
    GLOBX-17,Globex,expired,2024-01-01,2024-12-31,
    ```

2. Click **Submit**. NetBox reports "Imported 3 Support Contracts" and lists them.
3. Try importing two rows where the second has a status that isn't in the choice set, such as `cancelled`. The import is rejected with "Record 2 status: Select a valid choice." and neither row is created.

For a large file, enable **Background job** before submitting. The import then runs in NetBox's background worker, and NetBox links to the job so you can follow its progress.

### 3. Edit Several Contracts at Once

1. In the Support Contracts list, filter by vendor `Acme` and select ACME-2001 and ACME-2002.
2. Click **Edit Selected**.
3. Set **Status** to **Expired** and click **Apply**. NetBox reports "Updated 2 Support Contracts".

### 4. Export and Update by Re-importing

1. In the list, click **Export** and choose **All Data (CSV)**. The file has an **ID** column with each contract's ID.
2. The exported column headings are the table's column labels, such as `Contract number`, while imports expect field names, such as `contract_number`. Make an update file with field-name headings, containing just the IDs and the values to change:

    ```csv
    id,end_date
    12,2027-12-31
    13,2027-06-30
    ```

3. Import it. Rows with an `id` update those contracts, changing only the columns given; the list that follows shows the contracts that changed. On NetBox 4.6.5 and later, this needs change permission as well as add permission.

### 5. Import Routes That Reference Devices

1. Open **Static Routes**, then **Import**.
2. Paste routes that name their devices:

    ```csv
    destination,next_hop,device,metric
    10.30.0.0/16,192.0.2.1,core-sw-01,10
    10.40.0.0/16,192.0.2.1,core-sw-01,10
    ```

3. Click **Submit**. NetBox reports "Imported 2 Static Routes", each linked to the device named `core-sw-01`.

Object fields identify the objects they reference by a natural identifier, here the device's name. See [Import and Export](../import-export.md) for how that identifier is chosen, and for referencing custom objects, which usually have to be identified by ID.

## Result

Contracts and routes are loaded in bulk, changed in bulk, and updated from an exported file.

## See Also

- [Import and Export](../import-export.md) for the value format of every field type.
- [Keeping data clean](data-quality.md): imports are validated by the same rules as the edit form.

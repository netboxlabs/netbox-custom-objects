# Relating Custom Objects to Each Other

**Scenario:** you want to track software licenses. Each vendor sells products, products can be bundled into suites (Adobe Creative Cloud includes Photoshop and Acrobat), and each product has licenses. A license key only needs to be unique within its product.

**You'll use:** fields that reference another Custom Object Type, a field that references its own type, a reverse relation name, and a unique constraint over two fields.

## Before You Start

- The vendors exist in NetBox as manufacturers (**Devices > Manufacturers**), e.g. Adobe and Autodesk.
- Your user can add Custom Object Types, fields and constraints.

## Steps

### 1. Create the Software Product Type

1. Go to **Custom Objects > Custom Object Types** and click **Add**.
2. Set **Internal name** to `software_product`, **Display name (singular)** to `Software Product` and **Display name (plural)** to `Software Products`, then click **Create**.
3. Add these fields (**Add > Field** on the type's page):

    | Name | Label | Type | Display weight | Other settings |
    |------|-------|------|----------------|----------------|
    | `name` | Name | Text | 100 | **Required**, **Primary name field** |
    | `vendor` | Vendor | Object | 110 | **Required**; **Related object type:** DCIM > Manufacturer |
    | `suite` | Suite | Object | 120 | **Related object type:** Custom Objects > Software Product; **Reverse relation name:** `suite_products` |

The **Suite** field points at the type it belongs to, so a product can be part of another product.

### 2. Create the Software License Type

1. Create a second type with **Internal name** `software_license`, **Display name (singular)** `Software License` and **Display name (plural)** `Software Licenses`.
2. Add these fields:

    | Name | Label | Type | Display weight | Other settings |
    |------|-------|------|----------------|----------------|
    | `product` | Product | Object | 100 | **Required**; **Related object type:** Custom Objects > Software Product; **Reverse relation name:** `licenses`; **On delete behavior:** Protect |
    | `license_key` | License key | Text | 110 | **Required** |
    | `seats` | Seats | Integer | 120 | **Minimum value:** `1` |
    | `expires` | Expires | Date | 130 | |

**On delete behavior: Protect** stops a product from being deleted while it still has licenses.

### 3. Make License Keys Unique per Product

1. On the Software License type's page, click **Add** and choose **Constraint**.
2. Set **Name** to `unique_key_per_product` and **Type** to **Unique**.
3. Under **Fields**, choose **Product** and **License key**.
4. Click **Create**.

### 4. Name Licenses Usefully

Licenses have no field that makes a good name on its own. On the Software License type's page, click **Edit**, set **Display expression** to `{{ product }}: {{ license_key }}`, and save.

### 5. Add the Data

1. Add a product **Creative Cloud** (vendor Adobe), then **Photoshop** and **Acrobat**, each with **Suite** set to Creative Cloud. **Create & Add Another** keeps the form open between them.
2. Add two licenses for Photoshop, e.g. keys `PS-AAAA-1111` and `PS-BBBB-2222`. They're named after the display expression, e.g. **Photoshop: PS-AAAA-1111**.
3. Try adding a third Photoshop license with the key `PS-AAAA-1111`. Saving it fails with "Software License with this Product and License key already exists." Change its product to Acrobat and save again: the same key is accepted for a different product.

### 6. Navigate the Hierarchy

1. Open **Creative Cloud**. Its **Custom Objects** tab lists Photoshop and Acrobat (through **Suite**).
2. Open **Photoshop**. Its **Custom Objects** tab lists its licenses.
3. Open the Adobe manufacturer. Its **Custom Objects** tab lists the Adobe products, and its **Related Objects** panel links to them.
4. Try deleting Photoshop. NetBox refuses straight away and lists the licenses that still reference it.

## Result

Products, suites and licenses are linked in a hierarchy you can follow in both directions, and license keys are unique within each product.

## See Also

- [Field Attributes: Object Reference Fields](../field-attributes.md#object-reference-fields)
- [Constraints](../index.md#adding-constraints-to-the-custom-object-type)
- [GraphQL API](../graphql.md), for querying the whole hierarchy in one request.

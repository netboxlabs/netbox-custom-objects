# Custom Objects Tab

The **Custom Objects** tab on an object's detail page lists the custom objects that reference it through object or multi-object fields, including polymorphic fields.

The tab provides:

- A count of linked objects the user can view.
- Search, type and tag filters, sortable columns, pagination and configurable columns.
- Edit and delete actions for individual objects.

The tab is hidden when the user cannot view any linked objects. Changes to types, fields and objects appear on the next page load; no restart or additional configuration is required.

Loading may take longer for objects with many linked custom objects.

The Value column lists the objects each multi-object field links to, up to the [`max_multiobject_display`](configuration.md#max_multiobject_display) setting.

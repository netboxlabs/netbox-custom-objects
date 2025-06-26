# netbox-custom-objects
Custom Objects plugin

1. Add `netbox_custom_objects` to `PLUGINS` in `configuration.py`.

```python
PLUGINS = [
    # ...
    'netbox_custom_objects',
]
```

2. Run NetBox migrations:

```
$ ./manage.py migrate
```

## API

The three relevant models making up the Custom Objects system can be manipulated through CRUD operations using the 
standard NetBox API, using endpoints located at: `/api/plugins/custom-objects/`

```json
{
    "custom-object-type-fields": "http://127.0.0.1:8000/api/plugins/custom-objects/custom-object-type-fields/",
    "custom-object-types": "http://127.0.0.1:8000/api/plugins/custom-objects/custom-object-types/",
    "cats": "http://127.0.0.1:8000/api/plugins/custom-objects/cat/"
}
```

### Custom Object Types

Create a Custom Object Type with a POST call to `/api/plugins/custom-object/custom-object-types/` using a payload
similar to the following:

```json
{
  "name": "My Service Type",
}
```

### Custom Object Type Fields

Then define the schema of the Custom Object Type by creating fields of various types, with POST requests to
`/api/plugins/custom-objects/custom-object-type-fields/`:

```json
{
  "custom_object_type": 9,
  "name": "internal_id",
  "label": "Internal ID",
  "type": "integer",
  "options": {
    "min": 0,
    "max": 9999
  }
}
```

Available `type` values are: `char`, `integer`, `boolean`, `date`, `datetime`, `object`, and `multiobject`. Attributes for
specific field types can be specified using the `options` object (details TBD).

If the type is `object` or `multiobject`, the content type of the field is designated using the `app_label` and `model` attributes
as shown here:

```json
{
  "custom_object_type": 9,
  "name": "single_device",
  "label": "Single Device",
  "type": "object",
  "app_label": "dcim",
  "model": "device"
}
```

```json
{
  "custom_object_type": 9,
  "name": "device_list",
  "label": "Device List",
  "type": "multiobject",
  "app_label": "dcim",
  "model": "device"
}
```

!!! note
An `object` or `multiobject` field can point to any Custom Object, as well as any other existing object internal to NetBox.
Use an `app_label` of `netbox_custom_objects` and a `model` of `customobject`. 

### Custom Objects

Once the schema of a Custom Object Type is defined through its list of fields, you can create Custom Objects,
which are instances of Custom Object Types with specific values populated into the fields defined in the schema.
Create a Custom Object with a POST to `/api/plugins/custom-objects/custom-objects/`:

```json
{
  "custom_object_type": 9,
  "name": "My Object",
  "data": {
    "internal_id": 102,
    "device_list": [34, 1],
    "single_device": 16
  }
}
```

PATCH requests can be used to update all the above objects, as well as DELETE and GET operations, using the detail
URL for each model, i.e. `/api/plugins/custom-objects/custom-objects/15/`

# netbox-service-mappings
Service Mappings plugin

1. Add `netbox_service_mappings` to the end of `PLUGINS` in `configuration.py`.

```python
PLUGINS = [
    # ...
    'netbox_service_mappings',
]
```

2. Run NetBox migrations:

```
$ ./manage.py migrate
```

## API

The three relevant models making up the Service Mappings system can be manipulated through CRUD operations using the 
standard NetBox API, using endpoints located at: `/api/plugins/service-mappings/`

```json
{
    "mapping-type-fields": "http://127.0.0.1:8000/api/plugins/service-mappings/mapping-type-fields/",
    "mapping-types": "http://127.0.0.1:8000/api/plugins/service-mappings/mapping-types/",
    "mappings": "http://127.0.0.1:8000/api/plugins/service-mappings/mappings/"
}
```

### Service Mapping Types

Create a Service Mapping Type with a POST call to `/api/plugins/service-mappings/mapping-types/` using a payload
similar to the following:

```json
{
  "name": "My Service Type",
  "slug": "my-service-type"
}
```

### Mapping Type Fields

Then define the schema of the Service Mapping Type by creating fields of various types, with POST requests to
`/api/plugins/service-mappings/mapping-type-fields/`:

```json
{
  "mapping_type": 9,
  "name": "internal_id",
  "label": "Internal ID",
  "field_type": "integer",
  "options": {
    "min": 0,
    "max": 9999
  }
}
```

Available `field_type` values are: `char`, `integer`, `boolean`, `date`, `datetime`, and `object`. Attributes for
specific field types can be specified using the `options` object (details TBD).

If the type is `object`, the field can represent either a single object or a list of objects, controlled by
the `many` attribute. The content type of the field is designated using the `app_label` and `model` attributes
as shown here:

```json
{
  "mapping_type": 9,
  "name": "single_device",
  "label": "Single Device",
  "field_type": "object",
  "many": false,
  "app_label": "dcim",
  "model": "device"
}
```

```json
{
  "mapping_type": 9,
  "name": "device_list",
  "label": "Device List",
  "field_type": "object",
  "many": true,
  "app_label": "dcim",
  "model": "device"
}
```

!!! note
An `object` field can point to any Service Mapping object, as well as any other existing object internal to NetBox.
Use an `app_label` of `netbox_service_mappings` and a `model` of `servicemapping`. 

### Service Mappings

Once the schema of a Service Mapping Type is defined through its list of fields, you can create Service Mappings,
which are instances of Service Mapping Types with specific values populated into the fields defined in the schema.
Create a Service Mapping with a POST to `/api/plugins/service-mappings/mappings/`:

```json
{
  "mapping_type": 9,
  "name": "My Mapping",
  "data": {
    "internal_id": 102,
    "device_list": [34, 1],
    "single_device": 16
  }
}
```

PATCH requests can be used to update all the above objects, as well as DELETE and GET operations, using the detail
URL for each model, i.e. `/api/plugins/service-mappings/mappings/15/`

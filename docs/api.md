## API

The NetBox Custom Objects plugin provides CRUD operations through the standard NetBox API, with endpoints located at: `/api/plugins/custom-objects/`

```json
{
    "custom-object-types": "http://127.0.0.1:8000/api/plugins/custom-objects/custom-object-types/",
    "custom-object-type-fields": "http://127.0.0.1:8000/api/plugins/custom-objects/custom-object-type-fields/",
    "my-custom-type": "http://127.0.0.1:8000/api/plugins/custom-objects/my-custom-type/"
}
```

The plugin dynamically creates endpoints for each Custom Object Type you define. The endpoint names are based on the `name` of the Custom Object Type.

### Custom Object Types

Create a Custom Object Type with a POST call to `/api/plugins/custom-objects/custom-object-types/` using a payload
similar to the following:

```json
{
  "name": "Server",
  "description": "Server inventory objects",
  "verbose_name_plural": "Servers"
}
```

### Custom Object Type Fields

Define the schema of the Custom Object Type by creating fields of various types, with POST requests to
`/api/plugins/custom-objects/custom-object-type-fields/`:

```json
{
  "custom_object_type": 9,
  "name": "internal_id",
  "label": "Internal ID",
  "type": "integer",
  "required": true,
  "validation_minimum": 0,
  "validation_maximum": 9999
}
```

Available `type` values are:
- `text` - Short text field
- `longtext` - Long text field with textarea widget
- `integer` - Integer field
- `decimal` - Decimal field
- `boolean` - Boolean field
- `date` - Date field
- `datetime` - DateTime field
- `url` - URL field
- `json` - JSON field
- `select` - Single select from choice set
- `multiselect` - Multiple select from choice set
- `object` - Reference to a single object
- `multiobject` - Reference to multiple objects

Field-specific attributes can be specified using the validation and configuration fields:

#### Text Fields (`text`, `longtext`)
```json
{
  "custom_object_type": 9,
  "name": "hostname",
  "label": "Hostname",
  "type": "text",
  "required": true,
  "validation_regex": "^[a-zA-Z0-9-]+$"
}
```

#### Numeric Fields (`integer`, `decimal`)
```json
{
  "custom_object_type": 9,
  "name": "cpu_cores",
  "label": "CPU Cores",
  "type": "integer",
  "validation_minimum": 1,
  "validation_maximum": 128
}
```

#### Choice Fields (`select`, `multiselect`)
```json
{
  "custom_object_type": 9,
  "name": "environment",
  "label": "Environment",
  "type": "select",
  "choice_set": 5
}
```

#### Object Reference Fields (`object`, `multiobject`)

If the type is `object` or `multiobject`, the content type of the field is designated using the `app_label` and `model` attributes:

```json
{
  "custom_object_type": 9,
  "name": "primary_device",
  "label": "Primary Device",
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

> [!NOTE]
> An `object` or `multiobject` field can point to any Custom Object, as well as any other existing object internal to NetBox.  
> Use an `app_label` of `custom-objects` and a `model` of the Custom Object name to reference other custom objects.  


### Custom Objects

Once the schema of a Custom Object Type is defined through its list of fields, you can create Custom Objects,
which are instances of Custom Object Types with specific values populated into the fields defined in the schema.

Create a Custom Object with a POST to `/api/plugins/custom-objects/<custom-object-type>/` where `<custom-object-type>` is the name of your Custom Object Type:

```json
{
  "internal_id": 102,
  "hostname": "server-001",
  "cpu_cores": 8,
  "environment": "production",
  "device_list": [34, 1],
  "primary_device": 16
}
```

The response will include the created object with its assigned ID and additional metadata:

```json
{
  "id": 15,
  "url": "http://127.0.0.1:8000/api/plugins/custom-objects/server/15/",
  "custom_object_type": {
    "id": 9,
    "name": "Server",
    "description": "Server inventory objects"
  },
  "internal_id": 102,
  "hostname": "server-001",
  "cpu_cores": 8,
  "environment": "production",
  "device_list": [34, 1],
  "primary_device": 16,
  "tags": [],
  "created": "2024-01-15T10:30:00Z",
  "last_updated": "2024-01-15T10:30:00Z"
}
```

PATCH requests can be used to update all the above objects, as well as DELETE and GET operations, using the detail
URL for each model:
- Custom Object Types: `/api/plugins/custom-objects/custom-object-types/15/`
- Custom Object Type Fields: `/api/plugins/custom-objects/custom-object-type-fields/23/`
- Custom Objects: `/api/plugins/custom-objects/<custom-object-type>/15/`
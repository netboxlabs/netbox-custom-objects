# Installation

## Requirements

* NetBox v4.5.2 through v4.7.x (see [`COMPATIBILITY.md`](https://github.com/netboxlabs/netbox-custom-objects/blob/main/COMPATIBILITY.md) for the supported version matrix)
* Python 3.12 or later
* PostgreSQL (required — Custom Object Types are backed by real database tables)
* Redis (required — used for background jobs such as search reindexing, and for the cache that keeps NetBox's worker processes in step when a Custom Object Type is deleted)

Using Custom Objects with [NetBox Branching](branching.md) requires NetBox v4.6.2 or later and netbox-branching v1.0.4 or later.

## Installation

### 1. Install the Plugin

Add the Python package to NetBox's running environment:

```no-highlight
pip install netboxlabs-netbox-custom-objects
```

To keep the plugin installed when NetBox is upgraded, add it to `local_requirements.txt` in the NetBox root directory:

```no-highlight
echo netboxlabs-netbox-custom-objects >> local_requirements.txt
```

### 2. Enable the Plugin

Add `netbox_custom_objects` to `PLUGINS` in your `configuration.py`:

```python
PLUGINS = [
    # ...
    'netbox_custom_objects',
]
```

### 3. Run Database Migrations and Collect Static Files

Apply the plugin's database migrations and collect its static files:

```no-highlight
./manage.py migrate
./manage.py collectstatic
```

### 4. Restart NetBox

Restart NetBox's WSGI and background worker processes:

```no-highlight
sudo systemctl restart netbox netbox-rq
```

### NetBox Branching

No extra configuration is needed to use Custom Objects with NetBox Branching. See [Using Custom Objects with NetBox Branching](branching.md) for the requirements and what is supported.

## Next Steps

- [Configuration](configuration.md) lists the plugin's optional settings.
- [Permissions](permissions.md) explains how to grant users access to Custom Object Types and their objects.

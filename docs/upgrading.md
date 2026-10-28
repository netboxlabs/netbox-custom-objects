# Upgrading

Check the [compatibility matrix](https://github.com/netboxlabs/netbox-custom-objects/blob/main/COMPATIBILITY.md) before upgrading either NetBox or Custom Objects, and read the [release notes](releases.md) for the versions in between.

## Upgrade Steps

1. Back up the NetBox database.
2. Install the new release of the plugin, alongside (or after) the new NetBox release if you're upgrading NetBox too:

    ```no-highlight
    pip install --upgrade netboxlabs-netbox-custom-objects
    ```

    If the plugin is listed in `local_requirements.txt`, NetBox's `upgrade.sh` script installs it for you. Update any version pin before running the script.

3. Apply database migrations:

    ```no-highlight
    ./manage.py migrate
    ```

4. Collect static files, which include the plugin's in-app help pages:

    ```no-highlight
    ./manage.py collectstatic --no-input
    ```

5. Restart NetBox's WSGI and background worker processes:

    ```no-highlight
    sudo systemctl restart netbox netbox-rq
    ```

## Custom Object Type Tables

Each Custom Object Type has its own database table. When an upgrade adds columns shared by all custom objects, `migrate` updates the existing tables in main and in branches without pending migrations. Other branches are updated after their migrations run; see [Upgrades](branching.md#upgrades).

The `upgrade_custom_objects` management command runs the same check on demand:

```no-highlight
./manage.py upgrade_custom_objects             # check and update every type, in main and branches without pending migrations
./manage.py upgrade_custom_objects --dry-run   # only report what would change
./manage.py upgrade_custom_objects --cot dhcp_scope   # one type (by name or ID), main only
```

It adds new columns that allow empty values or have a default. A new column that needs a value and has no default, or a column that a release removed, is reported but never changed automatically.

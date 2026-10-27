# Upgrading

Check the [compatibility matrix](https://github.com/netboxlabs/netbox-custom-objects/blob/main/COMPATIBILITY.md) before upgrading either NetBox or Custom Objects, and read the [release notes](releases.md) for the versions in between.

## Upgrade Steps

1. Back up the NetBox database.
2. Install the new release of the plugin, alongside (or after) the new NetBox release if you're upgrading NetBox too:

    ```no-highlight
    pip install --upgrade netboxlabs-netbox-custom-objects
    ```

    If you installed the plugin through `local_requirements.txt`, NetBox's `upgrade.sh` script installs the latest release for you.

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

Each Custom Object Type has its own database table. When a new release of NetBox or Custom Objects adds columns that every custom object needs, `migrate` adds them to the existing tables, including in every [NetBox Branching](branching.md) branch.

The `upgrade_custom_objects` management command runs the same check on demand:

```no-highlight
./manage.py upgrade_custom_objects             # check and update every type, and every branch
./manage.py upgrade_custom_objects --dry-run   # only report what would change
./manage.py upgrade_custom_objects --cot dhcp_scope   # one type (by name or ID), main only
```

It adds new columns that allow empty values or have a default. A new column that needs a value and has no default, or a column that a release removed, is reported but never changed automatically.

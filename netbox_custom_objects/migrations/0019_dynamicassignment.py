from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_job_queue_name'),
        ('dcim', '0225_gfk_indexes'),
        ('tenancy', '0022_add_comments_to_organizationalmodel'),
        ('virtualization', '0052_gfk_indexes'),
        ("netbox_custom_objects", "0018_alter_customobjecttypefield_schema_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="DynamicAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                ("custom_field_data", models.JSONField(blank=True, default=dict)),
                ("name", models.CharField(max_length=100, unique=True, verbose_name="name")),
                ("weight", models.PositiveSmallIntegerField(default=1000, verbose_name="weight")),
                ("description", models.CharField(blank=True, max_length=200, verbose_name="description")),
                ("is_active", models.BooleanField(default=True, verbose_name="is active")),
                (
                    "assigned_object_types",
                    models.ManyToManyField(
                        help_text="The device or virtual machine types this assignment applies to.",
                        related_name="dynamic_assignments",
                        to="core.objecttype",
                        verbose_name="assigned object types",
                    ),
                ),
                ("regions", models.ManyToManyField(blank=True, related_name="+", to="dcim.region")),
                ("site_groups", models.ManyToManyField(blank=True, related_name="+", to="dcim.sitegroup")),
                ("sites", models.ManyToManyField(blank=True, related_name="+", to="dcim.site")),
                ("locations", models.ManyToManyField(blank=True, related_name="+", to="dcim.location")),
                ("device_types", models.ManyToManyField(blank=True, related_name="+", to="dcim.devicetype")),
                ("roles", models.ManyToManyField(blank=True, related_name="+", to="dcim.devicerole")),
                ("platforms", models.ManyToManyField(blank=True, related_name="+", to="dcim.platform")),
                (
                    "cluster_types",
                    models.ManyToManyField(blank=True, related_name="+", to="virtualization.clustertype"),
                ),
                (
                    "cluster_groups",
                    models.ManyToManyField(blank=True, related_name="+", to="virtualization.clustergroup"),
                ),
                ("clusters", models.ManyToManyField(blank=True, related_name="+", to="virtualization.cluster")),
                ("tenant_groups", models.ManyToManyField(blank=True, related_name="+", to="tenancy.tenantgroup")),
                ("tenants", models.ManyToManyField(blank=True, related_name="+", to="tenancy.tenant")),
            ],
            options={
                "verbose_name": "dynamic assignment",
                "verbose_name_plural": "dynamic assignments",
                "ordering": ("weight", "name"),
            },
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_concrete_objecttype"),
        ("netbox_custom_objects", "0004_customobjecttype_group_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="customobjecttypefield",
            name="is_polymorphic",
            field=models.BooleanField(
                default=False,
                verbose_name="polymorphic",
                help_text=(
                    "When enabled, this field uses a generic foreign key and may reference "
                    "objects of multiple types. Set the allowed types in 'Related object types'."
                ),
            ),
        ),
        migrations.AddField(
            model_name="customobjecttypefield",
            name="related_object_types",
            field=models.ManyToManyField(
                blank=True,
                related_name="polymorphic_custom_object_type_fields",
                to="core.objecttype",
                verbose_name="related object types",
                help_text=(
                    "The types of objects this polymorphic field may reference "
                    "(used when 'Polymorphic' is enabled)."
                ),
            ),
        ),
    ]

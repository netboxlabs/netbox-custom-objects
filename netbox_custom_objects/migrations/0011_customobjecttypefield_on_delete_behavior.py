from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_custom_objects", "0010_fix_object_fk_set_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="customobjecttypefield",
            name="on_delete_behavior",
            field=models.CharField(
                blank=True,
                choices=[
                    ("set_null", "Set null (clear the field, keep this object)"),
                    ("cascade", "Cascade (delete this object too)"),
                    ("protect", "Protect (prevent deletion of the referenced object)"),
                ],
                default="set_null",
                help_text=(
                    "What happens to this Custom Object when the referenced object is deleted "
                    "(applies to Object-type fields only). "
                    "Set null: clear the field and keep this object. "
                    "Cascade: delete this object too. "
                    "Protect: prevent deletion of the referenced object."
                ),
                max_length=20,
                verbose_name="on delete behavior",
            ),
        ),
    ]
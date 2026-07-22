from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0017_customobjecttype_display_expression'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customobjecttypefield',
            name='schema_id',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
    ]

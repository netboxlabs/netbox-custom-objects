from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0014_fix_mixed_case_field_names'),
    ]

    operations = [
        migrations.AddField(
            model_name='customobjecttype',
            name='display_expression',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]

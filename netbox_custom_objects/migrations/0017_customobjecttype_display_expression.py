from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0016_widen_integer_columns'),
    ]

    operations = [
        migrations.AddField(
            model_name='customobjecttype',
            name='display_expression',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
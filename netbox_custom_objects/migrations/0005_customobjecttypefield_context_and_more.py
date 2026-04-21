import django.core.validators
import re
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_datasource_sync_permission'),
        ('extras', '0137_default_ordering_indexes'),
        ('netbox_custom_objects', '0004_customobjecttype_group_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='customobjecttypefield',
            name='context',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='customobjecttypefield',
            name='related_name',
            field=models.CharField(
                blank=True,
                max_length=100,
                validators=[
                    django.core.validators.RegexValidator(
                        message='Only lowercase alphanumeric characters and underscores are allowed.',
                        regex='^[a-z0-9_]+$'
                    ),
                    django.core.validators.RegexValidator(
                        flags=re.RegexFlag['IGNORECASE'],
                        inverse_match=True,
                        message='Double underscores are not permitted in the reverse relation name.',
                        regex='__',
                    ),
                ]
            ),
        ),
        migrations.AlterField(
            model_name='customobjecttypefield',
            name='name',
            field=models.CharField(
                max_length=50,
                validators=[
                    django.core.validators.RegexValidator(
                        message='Only lowercase alphanumeric characters and underscores are allowed.',
                        regex='^[a-z0-9_]+$'
                    ),
                    django.core.validators.RegexValidator(
                        flags=re.RegexFlag['IGNORECASE'],
                        inverse_match=True,
                        message='Double underscores are not permitted in custom object field names.',
                        regex='__',
                    ),
                ]
            ),
        ),
        migrations.AddConstraint(
            model_name='customobjecttypefield',
            constraint=models.UniqueConstraint(
                condition=models.Q(('related_name__gt', '')),
                fields=('related_object_type', 'related_name'),
                name='netbox_custom_objects_customobjecttypefield_unique_related_name',
            ),
        ),
    ]

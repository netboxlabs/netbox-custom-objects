import re

import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
import netbox_custom_objects.models
import taggit.managers
import utilities.json
import utilities.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0012_job_object_type_optional'),
        ('extras', '0123_journalentry_kind_default'),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomObjectObjectType",
            fields=[],
            options={
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("contenttypes.contenttype",),
            managers=[
                (
                    "objects",
                    netbox_custom_objects.models.CustomObjectObjectTypeManager(),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CustomObject",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                (
                    "tags",
                    taggit.managers.TaggableManager(
                        through="extras.TaggedItem", to="extras.Tag"
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="CustomObjectType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "custom_field_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=utilities.json.CustomFieldJSONEncoder,
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.TextField(blank=True)),
                ("schema", models.JSONField(blank=True, default=dict)),
                ("verbose_name_plural", models.CharField(blank=True, max_length=100)),
                (
                    "tags",
                    taggit.managers.TaggableManager(
                        through="extras.TaggedItem", to="extras.Tag"
                    ),
                ),
            ],
            options={
                "verbose_name": "Custom Object Type",
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="CustomObjectTypeField",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                ("type", models.CharField(default="text", max_length=50)),
                ("primary", models.BooleanField(default=False)),
                (
                    "name",
                    models.CharField(
                        max_length=50,
                        validators=[
                            django.core.validators.RegexValidator(
                                flags=re.RegexFlag["IGNORECASE"],
                                message="Only alphanumeric characters and underscores are allowed.",
                                regex="^[a-z0-9_]+$",
                            ),
                            django.core.validators.RegexValidator(
                                flags=re.RegexFlag["IGNORECASE"],
                                inverse_match=True,
                                message="Double underscores are not permitted in custom field names.",
                                regex="__",
                            ),
                        ],
                    ),
                ),
                ("label", models.CharField(blank=True, max_length=50)),
                ("group_name", models.CharField(blank=True, max_length=50)),
                ("description", models.CharField(blank=True, max_length=200)),
                ("required", models.BooleanField(default=False)),
                ("unique", models.BooleanField(default=False)),
                ("search_weight", models.PositiveSmallIntegerField(default=1000)),
                ("filter_logic", models.CharField(default="loose", max_length=50)),
                ("default", models.JSONField(blank=True, null=True)),
                ("related_object_filter", models.JSONField(blank=True, null=True)),
                ("weight", models.PositiveSmallIntegerField(default=100)),
                ("validation_minimum", models.BigIntegerField(blank=True, null=True)),
                ("validation_maximum", models.BigIntegerField(blank=True, null=True)),
                (
                    "validation_regex",
                    models.CharField(
                        blank=True,
                        max_length=500,
                        validators=[utilities.validators.validate_regex],
                    ),
                ),
                ("ui_visible", models.CharField(default="always", max_length=50)),
                ("ui_editable", models.CharField(default="yes", max_length=50)),
                ("is_cloneable", models.BooleanField(default=False)),
                ("comments", models.TextField(blank=True)),
                (
                    "choice_set",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="choices_for_object_type",
                        to="extras.customfieldchoiceset",
                    ),
                ),
                (
                    "custom_object_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fields",
                        to="netbox_custom_objects.customobjecttype",
                    ),
                ),
                (
                    "related_object_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="core.objecttype",
                    ),
                ),
            ],
            options={
                "verbose_name": "custom object type field",
                "verbose_name_plural": "custom object type fields",
                "ordering": ["group_name", "weight", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="customobjecttype",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("name"),
                name="netbox_custom_objects_customobjecttype_name",
                violation_error_message="A Custom Object Type with this name already exists.",
            ),
        ),
        migrations.AddConstraint(
            model_name="customobjecttypefield",
            constraint=models.UniqueConstraint(
                fields=("name", "custom_object_type"),
                name="netbox_custom_objects_customobjecttypefield_unique_name",
            ),
        ),
    ]

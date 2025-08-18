import logging
import sys

from core.models import ObjectType
from django.contrib.contenttypes.models import ContentType
from extras.choices import CustomFieldTypeChoices
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.reverse import reverse

from netbox_custom_objects import field_types
from netbox_custom_objects.models import (CustomObject, CustomObjectType,
                                          CustomObjectTypeField)

logger = logging.getLogger('netbox_custom_objects.api.serializers')


__all__ = (
    "CustomObjectTypeSerializer",
    "CustomObjectSerializer",
)


class ContentTypeSerializer(NetBoxModelSerializer):
    class Meta:
        model = ContentType
        fields = (
            "id",
            "app_label",
            "model",
        )


class CustomObjectTypeFieldSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_custom_objects-api:customobjecttypefield-detail"
    )
    app_label = serializers.CharField(required=False)
    model = serializers.CharField(required=False)

    class Meta:
        model = CustomObjectTypeField
        fields = (
            # 'id', 'url', 'name', 'label', 'custom_object_type', 'field_type', 'content_type', 'many', 'options',
            "id",
            "name",
            "label",
            "custom_object_type",
            "type",
            "primary",
            "default",
            "choice_set",
            "validation_regex",
            "validation_minimum",
            "validation_maximum",
            "related_object_type",
            "app_label",
            "model",
        )

    def validate(self, attrs):
        app_label = attrs.pop("app_label", None)
        model = attrs.pop("model", None)
        if attrs["type"] in [
            CustomFieldTypeChoices.TYPE_OBJECT,
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
        ]:
            try:
                attrs["related_object_type"] = ObjectType.objects.get(
                    app_label=app_label, model=model
                )
            except ObjectType.DoesNotExist:
                raise ValidationError(
                    "Must provide valid app_label and model for object field type."
                )
        if attrs["type"] in [
            CustomFieldTypeChoices.TYPE_SELECT,
            CustomFieldTypeChoices.TYPE_MULTISELECT,
        ]:
            if not attrs.get("choice_set", None):
                raise ValidationError(
                    "Must provide choice_set with valid PK for select field type."
                )
        return super().validate(attrs)

    def create(self, validated_data):
        """
        Record the user who created the Custom Object as its owner.
        """
        return super().create(validated_data)

    def get_related_object_type(self, obj):
        if obj.related_object_type:
            return dict(
                id=obj.related_object_type.id,
                app_label=obj.related_object_type.app_label,
                model=obj.related_object_type.model,
            )


class CustomObjectTypeSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_custom_objects-api:customobjecttype-detail"
    )
    fields = CustomObjectTypeFieldSerializer(
        nested=True,
        read_only=True,
        many=True,
    )

    class Meta:
        model = CustomObjectType
        fields = [
            "id",
            "url",
            "name",
            "description",
            "tags",
            "created",
            "last_updated",
            "fields",
        ]
        brief_fields = ("id", "url", "name", "description")

    def create(self, validated_data):
        return super().create(validated_data)


# TODO: Remove or reduce to a stub (not needed as all custom object serializers are generated via get_serializer_class)
class CustomObjectSerializer(NetBoxModelSerializer):
    relation_fields = None

    url = serializers.SerializerMethodField()
    field_data = serializers.SerializerMethodField()
    custom_object_type = CustomObjectTypeSerializer(nested=True)

    class Meta:
        model = CustomObject
        fields = [
            "id",
            "url",
            "name",
            "display",
            "custom_object_type",
            "tags",
            "created",
            "last_updated",
            "data",
            "field_data",
        ]
        brief_fields = (
            "id",
            "url",
            "name",
            "custom_object_type",
        )

    def get_display(self, obj):
        return f"{obj.custom_object_type}: {obj.name}"

    def validate(self, attrs):
        return super().validate(attrs)

    def update_relation_fields(self, instance):
        # TODO: Implement this
        pass

    def create(self, validated_data):
        model = validated_data["custom_object_type"].get_model()
        instance = model.objects.create(**validated_data)

        return instance

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        # self.update_relation_fields(instance)
        return instance

    def get_url(self, obj):
        """
        Given an object, return the URL that hyperlinks to the object.

        May raise a `NoReverseMatch` if the `view_name` and `lookup_field`
        attributes are not configured to correctly match the URL conf.
        """
        # Unsaved objects will not yet have a valid URL.
        if hasattr(obj, "pk") and obj.pk in (None, ""):
            return None

        view_name = "plugins-api:netbox_custom_objects-api:customobject-detail"
        lookup_value = getattr(obj, "pk")
        kwargs = {
            "pk": lookup_value,
            "custom_object_type": obj.custom_object_type.name.lower(),
        }
        request = self.context["request"]
        format = self.context.get("format")
        return reverse(view_name, kwargs=kwargs, request=request, format=format)

    def get_field_data(self, obj):
        result = {}
        return result


def get_serializer_class(model):
    model_fields = model.custom_object_type.fields.all()

    # Create field list including all necessary fields
    base_fields = ["id", "url", "display", "created", "last_updated", "tags"]
    custom_field_names = [field.name for field in model_fields]
    all_fields = base_fields + custom_field_names

    meta = type(
        "Meta",
        (),
        {
            "model": model,
            "fields": all_fields,
            "brief_fields": ("id", "url", "display"),
        },
    )

    def get_url(self, obj):
        """Generate the API URL for this object"""
        if hasattr(obj, "pk") and obj.pk in (None, ""):
            return None

        view_name = "plugins-api:netbox_custom_objects-api:customobject-detail"
        lookup_value = getattr(obj, "pk")
        kwargs = {
            "pk": lookup_value,
            "custom_object_type": obj.custom_object_type.name.lower(),
        }
        request = self.context["request"]
        format = self.context.get("format")
        return reverse(view_name, kwargs=kwargs, request=request, format=format)

    def get_display(self, obj):
        """Get display representation of the object"""
        return str(obj)

    # Create basic attributes for the serializer
    attrs = {
        "Meta": meta,
        "__module__": "netbox_custom_objects.api.serializers",
        "url": serializers.SerializerMethodField(),
        "get_url": get_url,
        "display": serializers.SerializerMethodField(),
        "get_display": get_display,
    }

    for field in model_fields:
        field_type = field_types.FIELD_TYPE_CLASS[field.type]()
        try:
            attrs[field.name] = field_type.get_serializer_field(field)
        except NotImplementedError:
            logger.debug(
                "serializer: {} field is not implemented; using a default serializer field".format(field.name)
            )

    serializer_name = f"{model._meta.object_name}Serializer"
    serializer = type(
        serializer_name,
        (NetBoxModelSerializer,),
        attrs,
    )

    # Register the serializer in the current module so NetBox can find it
    current_module = sys.modules[__name__]
    setattr(current_module, serializer_name, serializer)

    return serializer

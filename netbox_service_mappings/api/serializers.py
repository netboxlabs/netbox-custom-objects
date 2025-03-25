from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from extras.choices import CustomFieldTypeChoices
from netbox.api.serializers import NetBoxModelSerializer
# from netbox_service_mappings.choices import MappingFieldTypeChoices
from netbox_service_mappings.models import CustomObject, CustomObjectType, CustomObjectTypeField, CustomObjectRelation
from utilities.api import get_serializer_for_model

__all__ = (
    'CustomObjectTypeSerializer',
    'CustomObjectSerializer',
)


class ContentTypeSerializer(NetBoxModelSerializer):
    class Meta:
        model = ContentType
        fields = ('id', 'app_label', 'model',)


class CustomObjectTypeFieldSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:customobjecttypefield-detail'
    )
    content_type = serializers.SerializerMethodField()
    app_label = serializers.CharField(required=False)
    model = serializers.CharField(required=False)

    class Meta:
        model = CustomObjectTypeField
        fields = (
            'id', 'url', 'name', 'label', 'custom_object_type', 'field_type', 'content_type', 'many', 'options',
            'app_label', 'model',
        )

    def validate(self, attrs):
        app_label = attrs.pop('app_label', None)
        model = attrs.pop('model', None)
        if attrs['field_type'] == 'object':
            try:
                attrs['content_type'] = ContentType.objects.get(app_label=app_label, model=model)
            except ContentType.DoesNotExist:
                raise ValidationError('Must provide valid app_label and model for object field type.')
        return super().validate(attrs)

    def create(self, validated_data):
        """
        Record the user who created the Custom Object as its owner.
        """
        return super().create(validated_data)

    def get_content_type(self, obj):
        if obj.content_type:
            return dict(
                id=obj.content_type.id,
                app_label=obj.content_type.app_label,
                model=obj.content_type.model,
            )


class CustomObjectTypeSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:customobjecttype-detail'
    )
    fields = CustomObjectTypeFieldSerializer(
        nested=True,
        read_only=True,
        many=True,
    )

    class Meta:
        model = CustomObjectType
        fields = [
            'id', 'url', 'name', 'slug', 'description', 'tags', 'created', 'last_updated', 'fields',
        ]
        brief_fields = ('id', 'url', 'name', 'description')

    def create(self, validated_data):
        return super().create(validated_data)


class CustomObjectSerializer(NetBoxModelSerializer):
    relation_fields = None

    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:customobject-detail'
    )
    field_data = serializers.SerializerMethodField()
    custom_object_type = CustomObjectTypeSerializer(nested=True)

    class Meta:
        model = CustomObject
        fields = [
            'id', 'url', 'name', 'custom_object_type', 'tags', 'created', 'last_updated', 'data', 'field_data',
        ]
        brief_fields = ('id', 'url', 'name', 'custom_object_type',)

    def validate(self, attrs):
        self.relation_fields = {}
        for field in attrs['custom_object_type'].fields.filter(field_type=CustomFieldTypeChoices.TYPE_OBJECT):
            self.relation_fields[field.name] = attrs['data'].pop(field.name, None)
        return super().validate(attrs)

    def update_relation_fields(self, instance):
        for field_name, value in self.relation_fields.items():
            field = instance.custom_object_type.fields.get(name=field_name)
            if field.many:
                CustomObjectRelation.objects.filter(custom_object=instance, field=field).exclude(object_id__in=value).delete()
                for object_id in value:
                    resolved_object = field.model_class.objects.get(pk=object_id)
                    relation, _ = CustomObjectRelation.objects.get_or_create(
                        custom_object=instance,
                        field=field,
                        object_id=resolved_object.id,
                    )
            else:
                CustomObjectRelation.objects.filter(custom_object=instance, field=field).exclude(object_id=value).delete()
                resolved_object = field.model_class.objects.get(pk=value)
                relation, _ = CustomObjectRelation.objects.get_or_create(
                    custom_object=instance,
                    field=field,
                    object_id=resolved_object.id,
                )

    def create(self, validated_data):
        instance = super().create(validated_data)
        self.update_relation_fields(instance)
        return instance

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        self.update_relation_fields(instance)
        return instance

    def get_field_data(self, obj):
        result = {}
        for field_name, value in obj.fields.items():
            field = obj.custom_object_type.fields.get(name=field_name)
            if field.field_type == 'object':
                serializer = get_serializer_for_model(field.model_class)
                context = {'request': self.context['request']}
                result[field.name] = serializer(value, nested=True, context=context, many=field.many).data
                continue
            result[field_name] = obj.get_field_value(field_name)
        return result


class CustomObjectRelationSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:customobjectrelation-detail'
    )
    instance = serializers.SerializerMethodField(
        read_only=True
    )
    field = CustomObjectTypeFieldSerializer(
        read_only=True
    )
    custom_object = CustomObjectSerializer(
        read_only=True,
        nested=True,
    )

    class Meta:
        model = CustomObjectRelation
        fields = ('custom_object', 'field', 'object_id', 'instance',)

    def get_field(self, obj):
        context = {'request': self.context['request']}
        return CustomObjectTypeFieldSerializer(obj.field, context=context).data

    def get_instance(self, obj):
        if obj.instance:
            serializer = get_serializer_for_model(obj.instance)
            context = {'request': self.context['request']}
            return serializer(obj.instance, nested=True, context=context).data

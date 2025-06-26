from django.conf import settings
from django.forms import BoundField
from django.urls import reverse
from utilities.forms.fields.dynamic import (DynamicModelChoiceField,
                                            DynamicModelMultipleChoiceField)

from netbox_custom_objects.utilities import get_viewname

__all__ = ("CustomObjectDynamicModelChoiceField",)


class CustomObjectDynamicModelChoiceField(DynamicModelChoiceField):
    def get_bound_field(self, form, field_name):
        bound_field = BoundField(form, self, field_name)
        widget = bound_field.field.widget

        # Set initial value based on prescribed child fields (if not already set)
        if not self.initial and self.initial_params:
            filter_kwargs = {}
            for kwarg, child_field in self.initial_params.items():
                value = form.initial.get(child_field.lstrip("$"))
                if value:
                    filter_kwargs[kwarg] = value
            if filter_kwargs:
                self.initial = self.queryset.filter(**filter_kwargs).first()

        # Modify the QuerySet of the field before we return it. Limit choices to any data already bound: Options
        # will be populated on-demand via the APISelect widget.
        data = bound_field.value()

        if data:
            # When the field is multiple choice pass the data as a list if it's not already
            if (
                isinstance(bound_field.field, DynamicModelMultipleChoiceField)
                and type(data) is not list
            ):
                data = [data]

            field_name = getattr(self, "to_field_name") or "pk"
            filter = self.filter(field_name=field_name)
            try:
                self.queryset = filter.filter(self.queryset, data)
            except (TypeError, ValueError):
                # Catch any error caused by invalid initial data passed from the user
                self.queryset = self.queryset.none()
        else:
            self.queryset = self.queryset.none()

        # Normalize the widget choices to a list to accommodate the "null" option, if set
        if self.null_option:
            widget.choices = [
                (settings.FILTERS_NULL_CHOICE_VALUE, self.null_option),
                *[c for c in widget.choices],
            ]

        # Set the data URL on the APISelect widget (if not already set)
        if not widget.attrs.get("data-url"):
            viewname = get_viewname(self.queryset.model, action="list", rest_api=True)
            widget.attrs["data-url"] = reverse(
                viewname,
                kwargs={
                    "custom_object_type": form.instance.custom_object_type.name.lower()
                },
            )

        # Include quick add?
        if self.quick_add:
            viewname = get_viewname(self.model, "add")
            widget.quick_add_context = {
                "url": reverse(
                    viewname,
                    kwargs={
                        "custom_object_type": form.instance.custom_object_type.name.lower()
                    },
                ),
                "params": {},
            }
            for k, v in self.quick_add_params.items():
                if v == "$pk":
                    # Replace "$pk" token with the primary key of the form's instance (if any)
                    if getattr(form.instance, "pk", None):
                        widget.quick_add_context["params"][k] = form.instance.pk
                else:
                    widget.quick_add_context["params"][k] = v

        return bound_field

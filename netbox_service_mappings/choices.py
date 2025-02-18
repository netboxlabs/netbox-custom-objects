from django.utils.translation import gettext_lazy as _

from utilities.choices import ChoiceSet


class MappingFieldTypeChoices(ChoiceSet):
    CHAR = 'char'
    INTEGER = 'integer'
    BOOLEAN = 'boolean'
    OBJECT = 'object'

    CHOICES = (
        (CHAR, _('String'), 'cyan'),
        (INTEGER, _('Integer'), 'orange'),
        (BOOLEAN, _('Boolean'), 'green'),
        (OBJECT, _('Object'), 'orange'),
    )

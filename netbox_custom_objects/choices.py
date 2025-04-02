from django.utils.translation import gettext_lazy as _

from utilities.choices import ChoiceSet


class MappingFieldTypeChoices(ChoiceSet):
    CHAR = 'char'
    INTEGER = 'integer'
    BOOLEAN = 'boolean'
    DATE = 'date'
    DATETIME = 'datetime'
    OBJECT = 'object'

    CHOICES = (
        (CHAR, _('String'), 'cyan'),
        (INTEGER, _('Integer'), 'orange'),
        (BOOLEAN, _('Boolean'), 'green'),
        (DATE, _('Date'), 'red'),
        (DATETIME, _('DateTime'), 'blue'),
        (OBJECT, _('Object'), 'orange'),
    )

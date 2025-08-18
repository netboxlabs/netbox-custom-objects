from django.utils.translation import gettext_lazy as _
from utilities.choices import ChoiceSet


class MappingFieldTypeChoices(ChoiceSet):
    CHAR = "char"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    OBJECT = "object"

    CHOICES = (
        (CHAR, _("String"), "cyan"),
        (INTEGER, _("Integer"), "orange"),
        (BOOLEAN, _("Boolean"), "green"),
        (DATE, _("Date"), "red"),
        (DATETIME, _("DateTime"), "blue"),
        (OBJECT, _("Object"), "orange"),
    )


#
# Search
#

class SearchWeightChoices(ChoiceSet):
    WEIGHT_NONE = 0
    WEIGHT_LOW = 1000
    WEIGHT_MEDIUM = 500
    WEIGHT_HIGH = 100

    CHOICES = (
        (WEIGHT_HIGH, _('High (100)')),
        (WEIGHT_MEDIUM, _('Medium (500)')),
        (WEIGHT_LOW, _('Low (1000)')),
        (WEIGHT_NONE, _('Not searchable')),
    )

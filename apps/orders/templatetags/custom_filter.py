# apps/orders/templatetags/custom_filters.py
from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Allows to access dictionary items by key in templates.
    Usage: {{ my_dictionary|get_item:my_key }}
    """
    return dictionary.get(key)

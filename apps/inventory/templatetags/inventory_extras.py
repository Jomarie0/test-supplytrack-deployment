from django import template
register = template.Library()

@register.filter
def indent(level):
    """Indent visually based on hierarchy depth."""
    return "&nbsp;&nbsp;&nbsp;" * int(level)

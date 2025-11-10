from django import template
import json

register = template.Library()


@register.filter
def change_summary(changes):
    if not changes:
        return "-"
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except json.JSONDecodeError:
            return changes
    if isinstance(changes, dict):
        result = []
        for field, diff in changes.items():
            if isinstance(diff, dict):
                old = diff.get("old", "")
                new = diff.get("new", "")
                result.append(f"{field}: {old} → {new}")
            else:
                result.append(f"{field}: {diff}")
        return ", ".join(result)
    return str(changes)


@register.filter
def pretty_json(value):
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, indent=2, ensure_ascii=False)

from decimal import Decimal
from django.forms.models import model_to_dict


def compute_instance_diff(old_inst, new_inst, fields=None):
    """
    Return a small dict describing differences between old_inst and new_inst.
    Format:
      { "field": {"before": val, "after": val, "delta": num?, "op": "increased|decreased|changed|added|removed"} }
    - fields: list of field names to compare. If None, uses simple model fields.
    """
    if old_inst is None and new_inst is None:
        return {}

    def _simple_dict(inst, keys=None):
        if inst is None:
            return {}
        # Use model_to_dict to handle Decimal, ForeignKey -> id etc.
        d = model_to_dict(inst)
        if keys:
            return {k: d.get(k) for k in keys}
        return d

    before = _simple_dict(old_inst, fields)
    after = _simple_dict(new_inst, fields)
    keys = fields or sorted(set(before.keys()) | set(after.keys()))
    changes = {}

    for k in keys:
        b = before.get(k)
        a = after.get(k)
        if b == a:
            continue
        entry = {"before": b, "after": a}
        # try numeric delta
        try:
            if b is not None and a is not None:
                db = Decimal(str(b))
                da = Decimal(str(a))
                delta = da - db
                entry["delta"] = float(delta)
                if delta > 0:
                    entry["op"] = "increased"
                elif delta < 0:
                    entry["op"] = "decreased"
                else:
                    entry["op"] = "changed"
            else:
                entry["op"] = "added" if b is None else "removed"
        except Exception:
            entry["op"] = "changed"
        changes[k] = entry

    return changes

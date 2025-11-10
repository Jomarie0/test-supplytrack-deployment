from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from .models import AuditLog

@staff_member_required
def auditlog_list(request):
    """
    Staff-only view: list AuditLog entries with simple filters and pagination.
    GET params:
      - action: exact action string
      - user: user id
      - content: 'app_label.model' or content_type id
      - q: free-text search against object_repr
      - from: YYYY-MM-DD (date >=)
      - to: YYYY-MM-DD (date <=)
      - page: page number
    """
    qs = AuditLog.objects.select_related('user', 'content_type').all().order_by('-timestamp')

    action = request.GET.get('action')
    user_id = request.GET.get('user')
    content = request.GET.get('content')
    q = request.GET.get('q')
    date_from = request.GET.get('from')
    date_to = request.GET.get('to')

    if action:
        qs = qs.filter(action=action)
    if user_id:
        qs = qs.filter(user_id=user_id)
    if content:
        if '.' in content:
            app_label, model = content.split('.', 1)
            try:
                ct = ContentType.objects.get(app_label=app_label.lower(), model=model.lower())
                qs = qs.filter(content_type=ct)
            except ContentType.DoesNotExist:
                pass
        else:
            try:
                ct = ContentType.objects.get(pk=int(content))
                qs = qs.filter(content_type=ct)
            except Exception:
                pass
    if q:
        qs = qs.filter(object_repr__icontains=q)
    if date_from:
        qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:
        qs = qs.filter(timestamp__date__lte=date_to)

    paginator = Paginator(qs, 12)
    page_num = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_num)

    # small sets for filter UIs (not exhaustive)
    actions = [a[0] for a in AuditLog.ACTION_CHOICES]
    content_types = ContentType.objects.filter(pk__in=qs.values_list('content_type', flat=True).distinct())

    context = {
        'logs': page_obj,
        'actions': actions,
        'content_types': content_types,
        'filters': {
            'action': action or '',
            'user': user_id or '',
            'content': content or '',
            'q': q or '',
            'from': date_from or '',
            'to': date_to or '',
        }
    }
    return render(request, 'transactions/auditlog_list.html', context)
def clear_audit_logs(request):
    if request.method == "POST":
        AuditLog.objects.all().delete()
        messages.success(request, "All audit logs have been cleared successfully.")
        return redirect("transactions:auditlog_list")
    else:
        # prevent GET deletion (safety)
        messages.error(request, "Invalid request.")
        return redirect("transactions:auditlog_list")

# apps/purchasing/utils.py - NEW FILE

from django.core.mail import send_mail
from django.conf import settings
from .models import PurchaseOrderAudit, PurchaseOrderNotification


def log_po_action(purchase_order, action, user, request=None, notes='', previous_data=None, new_data=None):
    """
    Centralized function to log PO audit trail.
    
    Usage:
        log_po_action(
            purchase_order=po,
            action='price_rejected',
            user=request.user,
            request=request,
            notes="Price too high - counter-offered",
            previous_data={'total_cost': 1000},
            new_data={'total_cost': 850}
        )
    """
    audit_log = PurchaseOrderAudit.objects.create(
        purchase_order=purchase_order,
        user=user,
        action=action,
        previous_data=previous_data,
        new_data=new_data,
        notes=notes
    )
    
    if request:
        audit_log.ip_address = request.META.get('REMOTE_ADDR')
        audit_log.user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]
        audit_log.save()
    
    return audit_log


def create_po_notification(purchase_order, action_label):
    """Helper to create a notification row for a purchase order."""
    supplier_name = purchase_order.supplier_profile.company_name if purchase_order.supplier_profile else None
    PurchaseOrderNotification.objects.create(
        purchase_order=purchase_order,
        supplier_name=supplier_name,
        status=purchase_order.status,
        message=f"PO {purchase_order.purchase_order_id} {action_label}",
        payment_due_date=purchase_order.payment_due_date if purchase_order.pay_later else None,
    )


def send_po_email(purchase_order, email_type='request'):
    """
    Send email notifications for PO lifecycle events.
    
    Args:
        purchase_order: The PurchaseOrder instance
        email_type: One of: 'request', 'price_rejected', 'confirmed', 'shipped', 'overdue'
    """
    supplier_profile = purchase_order.supplier_profile
    supplier_email = supplier_profile.user.email if supplier_profile else None
    
    if not supplier_email:
        print(f"No email found for supplier {supplier_profile.company_name if supplier_profile else 'Unknown'}")
        return False
    
    # Build items list
    items_text = ""
    for item in purchase_order.items.all():
        item_name = item.product_variant.product.name if item.product_variant else item.product_name_text
        items_text += f"- {item.quantity_ordered}x {item_name}\n"
    
    # Email templates
    email_templates = {
        'request': {
            'subject': f"Purchase Order {purchase_order.purchase_order_id} - PRICE QUOTATION REQUEST",
            'message': f"""Dear {supplier_profile.company_name},

We have a new Purchase Order ({purchase_order.purchase_order_id}) requiring your pricing and confirmation.

**ACTION REQUIRED:** Please log into your supplier dashboard to view the requested products/quantities and submit your unit prices.

ITEMS REQUESTED:
{items_text}

Expected Delivery Date: {purchase_order.expected_delivery_date or 'TBD'}
Notes: {purchase_order.notes or 'No additional notes.'}

Best regards,
SupplyTrack Team"""
        },
        'price_rejected': {
            'subject': f"Purchase Order {purchase_order.purchase_order_id} - PRICE REVISION REQUESTED",
            'message': f"""Dear {supplier_profile.company_name},

Thank you for submitting your prices for PO {purchase_order.purchase_order_id}.

Unfortunately, we need to request a price revision due to the following:

{purchase_order.rejection_reason}

Please review and submit revised pricing at your earliest convenience.

ITEMS:
{items_text}

Thank you for your understanding.

Best regards,
SupplyTrack Team"""
        },
        'confirmed': {
            'subject': f"Purchase Order {purchase_order.purchase_order_id} - ORDER CONFIRMED",
            'message': f"""Dear {supplier_profile.company_name},

Great news! Your quote has been accepted and PO {purchase_order.purchase_order_id} is now CONFIRMED.

Order Total: ₱{purchase_order.total_cost}
Payment Method: {purchase_order.get_payment_method_display()}
Expected Delivery: {purchase_order.expected_delivery_date}

Please prepare the shipment and update the status when dispatched.

CONFIRMED ITEMS:
{items_text}

Thank you for your partnership.

Best regards,
SupplyTrack Team"""
        },
        'overdue': {
            'subject': f"URGENT: Payment Overdue for PO {purchase_order.purchase_order_id}",
            'message': f"""Dear {supplier_profile.company_name},

This is a reminder that payment for PO {purchase_order.purchase_order_id} is now OVERDUE.

Amount Due: ₱{purchase_order.balance_due}
Original Due Date: {purchase_order.payment_due_date}
Days Overdue: {purchase_order.days_overdue}

Please contact our accounts department to resolve this matter immediately.

Best regards,
SupplyTrack Finance Team"""
        }
    }
    
    template = email_templates.get(email_type)
    if not template:
        return False
    
    try:
        send_mail(
            subject=template['subject'],
            message=template['message'],
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[supplier_email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def update_payment_status(purchase_order):
    """
    Automatically update payment status based on current state.
    Should be called when:
    - Payment is recorded
    - Payment due date passes
    - PO is confirmed/received
    """
    from django.utils import timezone
    
    # If fully paid
    if purchase_order.amount_paid >= purchase_order.total_cost:
        purchase_order.payment_status = 'paid'
    
    # If partially paid
    elif purchase_order.amount_paid > 0:
        purchase_order.payment_status = 'partial'
    
    # Check for overdue (Net 30 only)
    elif (purchase_order.payment_method == 'net_30' and 
          purchase_order.payment_due_date and 
          timezone.now().date() > purchase_order.payment_due_date):
        purchase_order.payment_status = 'overdue'
    
    # Otherwise pending
    else:
        if purchase_order.payment_status not in ['paid', 'partial']:
            purchase_order.payment_status = 'pending'
    
    purchase_order.save(update_fields=['payment_status'])


def calculate_po_metrics(purchase_orders_qs):
    """
    Calculate key metrics for a queryset of purchase orders.
    
    Returns:
        dict: Dictionary of metrics including:
            - total_value
            - avg_negotiation_rounds
            - on_time_rate
            - overdue_count
            - etc.
    """
    from django.db.models import Avg, Count, Sum, Q
    from django.utils import timezone
    
    metrics = {
        'total_value': purchase_orders_qs.aggregate(Sum('total_cost'))['total_cost__sum'] or 0,
        'total_count': purchase_orders_qs.count(),
        'avg_negotiation_rounds': purchase_orders_qs.aggregate(Avg('negotiation_rounds'))['negotiation_rounds__avg'] or 0,
        'confirmed_value': purchase_orders_qs.filter(
            status__in=['confirmed', 'in_transit', 'received']
        ).aggregate(Sum('total_cost'))['total_cost__sum'] or 0,
        'pending_action_count': purchase_orders_qs.filter(
            status__in=['request_pending', 'supplier_priced', 'price_revision']
        ).count(),
        'overdue_payment_count': purchase_orders_qs.filter(
            payment_status='overdue'
        ).count(),
        'overdue_payment_value': purchase_orders_qs.filter(
            payment_status='overdue'
        ).aggregate(Sum('total_cost'))['total_cost__sum'] or 0,
    }
    
    # Calculate on-time delivery rate
    completed = purchase_orders_qs.filter(status='received')
    on_time = completed.filter(
        received_date__lte=models.F('expected_delivery_date')
    ).count()
    metrics['on_time_rate'] = (on_time / completed.count() * 100) if completed.count() > 0 else 0
    
    return metrics


def get_supplier_performance(supplier_profile, days=30):
    """
    Calculate performance metrics for a specific supplier.
    
    Args:
        supplier_profile: SupplierProfile instance
        days: Number of days to look back (default: 30)
    
    Returns:
        dict: Performance metrics
    """
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Avg, Count, Sum
    
    cutoff_date = timezone.now() - timedelta(days=days)
    
    pos = supplier_profile.purchase_orders.filter(
        order_date__gte=cutoff_date,
        is_deleted=False
    )
    
    completed = pos.filter(status='received')
    
    performance = {
        'total_orders': pos.count(),
        'total_value': pos.aggregate(Sum('total_cost'))['total_cost__sum'] or 0,
        'avg_negotiation_rounds': pos.aggregate(Avg('negotiation_rounds'))['negotiation_rounds__avg'] or 0,
        'completion_rate': (completed.count() / pos.count() * 100) if pos.count() > 0 else 0,
        'on_time_deliveries': completed.filter(
            received_date__lte=models.F('expected_delivery_date')
        ).count(),
        'late_deliveries': completed.filter(
            received_date__gt=models.F('expected_delivery_date')
        ).count(),
        'cancelled_orders': pos.filter(status='cancelled').count(),
    }
    
    # Calculate average response time (time from request_pending to supplier_priced)
    # This would require storing timestamps for each status change
    # For now, we'll leave it as a placeholder
    performance['avg_response_time_hours'] = None  # Implement if needed
    
    return performance
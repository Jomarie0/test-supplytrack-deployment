from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count, Sum, Avg, F, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal

from apps.inventory.models import Product, Category, DemandCheckLog
from apps.purchase_orders.models import PurchaseOrder
from apps.delivery.models import Delivery
from apps.transactions.models import AuditLog


@login_required
def reports_dashboard(request):
    """Main reports dashboard with overview"""
    context = {
        'page_title': 'Reports Dashboard',
    }
    return render(request, 'reports/reports_dashboard.html', context)


@login_required
def inventory_report(request):
    """Inventory report with stock levels, reorder alerts, etc."""
    # Get filter parameters
    category_filter = request.GET.get('category', '')
    low_stock_only = request.GET.get('low_stock', '') == 'on'
    search_query = request.GET.get('search', '')
    
    # Base queryset + latest forecasted quantity from DemandCheckLog
    products = Product.objects.filter(is_deleted=False)
    latest_forecast_subq = (
        DemandCheckLog.objects.filter(product_id=OuterRef('pk'))
        .order_by('-checked_at')
        .values('forecasted_quantity')[:1]
    )
    products = products.annotate(
        forecast_qty=Subquery(latest_forecast_subq, output_field=IntegerField())
    )
    
    # Apply filters
    if category_filter:
        try:
            parent_category = Category.objects.get(pk=category_filter)
            category_ids = parent_category.get_descendant_ids()  # includes parent and all children
            products = products.filter(category_id__in=category_ids)
        except Category.DoesNotExist:
            products = products.none()
    
    if search_query:
        products = products.filter(
            Q(name__icontains=search_query) |
            Q(product_id__icontains=search_query)
        )
    
    if low_stock_only:
        products = products.filter(
            forecast_qty__isnull=False,
            stock_quantity__lte=F('forecast_qty')
        )
    
    # Calculate statistics
    total_products = products.count()
    total_stock_value = sum(p.stock_quantity * p.price for p in products if p.price)
    low_stock_count = products.filter(
        forecast_qty__isnull=False,
        stock_quantity__lte=F('forecast_qty')
    ).count()
    
    # Get categories for filter dropdown
    categories = Category.objects.active().order_by('name')
    
    context = {
        'products': products,
        'categories': categories,
        'total_products': total_products,
        'total_stock_value': total_stock_value,
        'low_stock_count': low_stock_count,
        'category_filter': category_filter,
        'low_stock_only': low_stock_only,
        'search_query': search_query,
        'page_title': 'Inventory Report',
    }
    return render(request, 'reports/inventory_report.html', context)


@login_required
def purchase_orders_report(request):
    """Purchase orders report"""
    # Get filter parameters
    status_filter = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    supplier_filter = request.GET.get('supplier', '')
    
    # Base queryset
    purchase_orders = PurchaseOrder.objects.filter(is_deleted=False)
    
    # Apply filters
    if status_filter:
        purchase_orders = purchase_orders.filter(status=status_filter)
    
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
            purchase_orders = purchase_orders.filter(created_at__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
            purchase_orders = purchase_orders.filter(created_at__lte=date_to_obj)
        except ValueError:
            pass
    
    if supplier_filter:
        purchase_orders = purchase_orders.filter(supplier_profile_id=supplier_filter)
    
    # Calculate statistics
    total_orders = purchase_orders.count()
    total_value = purchase_orders.aggregate(total=Sum('total_cost'))['total'] or Decimal('0.00')
    
    # Status breakdown
    status_breakdown = purchase_orders.values('status').annotate(
        count=Count('id'),
        total_value=Sum('total_cost')
    )
    
    context = {
        'purchase_orders': purchase_orders,
        'total_orders': total_orders,
        'total_value': total_value,
        'status_breakdown': status_breakdown,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
        'supplier_filter': supplier_filter,
        'page_title': 'Purchase Orders Report',
    }
    return render(request, 'reports/purchase_orders_report.html', context)


@login_required
def delivery_report(request):
    """Delivery performance report"""
    # Get filter parameters
    status_filter = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Base queryset
    deliveries = Delivery.objects.all()
    
    # Apply filters
    if status_filter:
        deliveries = deliveries.filter(delivery_status=status_filter)
    
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            deliveries = deliveries.filter(delivered_at__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            deliveries = deliveries.filter(delivered_at__lte=date_to_obj)
        except ValueError:
            pass
    
    # Calculate statistics
    total_deliveries = deliveries.count()
    delivered_count = deliveries.filter(delivery_status=Delivery.DELIVERED).count()
    delivery_rate = (delivered_count / total_deliveries * 100) if total_deliveries > 0 else 0
    
    # Status breakdown
    status_breakdown = deliveries.values('delivery_status').annotate(count=Count('id'))
    
    context = {
        'deliveries': deliveries,
        'total_deliveries': total_deliveries,
        'delivered_count': delivered_count,
        'delivery_rate': delivery_rate,
        'status_breakdown': status_breakdown,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
        'page_title': 'Delivery Report',
    }
    return render(request, 'reports/delivery_report.html', context)


@login_required
def audit_report(request):
    """Audit log report"""
    # Get filter parameters
    action_filter = request.GET.get('action', '')
    user_filter = request.GET.get('user', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Base queryset
    audit_logs = AuditLog.objects.all()
    
    # Apply filters
    if action_filter:
        audit_logs = audit_logs.filter(action=action_filter)
    
    if user_filter:
        audit_logs = audit_logs.filter(user_id=user_filter)
    
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            audit_logs = audit_logs.filter(timestamp__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            audit_logs = audit_logs.filter(timestamp__lte=date_to_obj)
        except ValueError:
            pass
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(audit_logs, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Statistics
    total_logs = audit_logs.count()
    action_breakdown = audit_logs.values('action').annotate(count=Count('id'))
    
    context = {
        'audit_logs': page_obj,
        'total_logs': total_logs,
        'action_breakdown': action_breakdown,
        'action_filter': action_filter,
        'user_filter': user_filter,
        'date_from': date_from,
        'date_to': date_to,
        'page_title': 'Audit Report',
    }
    return render(request, 'reports/audit_report.html', context)


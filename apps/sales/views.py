from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count, Sum, Avg, F
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal

from apps.orders.models import Order, OrderItem, ManualOrder, ManualOrderItem
from apps.inventory.models import Product
from apps.users.models import User


@login_required
def sales_dashboard(request):
    """Main sales dashboard with overview"""
    context = {
        'page_title': 'Sales Dashboard',
    }
    return render(request, 'sales/sales_dashboard.html', context)


@login_required
def sales_overview(request):
    """Sales overview with revenue, orders, AOV, etc."""
    # Get filter parameters
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    status_filter = request.GET.get('status', '')
    
    # Base querysets - only completed/paid orders
    orders = Order.objects.filter(
        is_deleted=False,
        status__in=['Completed', 'Shipped'],
        payment_status='paid'
    )
    manual_orders = ManualOrder.objects.filter(
        is_deleted=False,
        status__in=['Completed', 'Shipped'],
        payment_status='paid'
    )
    
    # Apply date filters
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            orders = orders.filter(order_date__gte=date_from_obj)
            manual_orders = manual_orders.filter(order_date__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            orders = orders.filter(order_date__lte=date_to_obj)
            manual_orders = manual_orders.filter(order_date__lte=date_to_obj)
        except ValueError:
            pass
    
    # Calculate statistics
    total_orders = orders.count() + manual_orders.count()
    
    # Calculate revenue
    orders_revenue = sum(order.get_total_cost for order in orders)
    manual_orders_revenue = sum(order.get_total_cost for order in manual_orders)
    total_revenue = orders_revenue + manual_orders_revenue
    
    # Average Order Value
    aov = (total_revenue / total_orders) if total_orders > 0 else Decimal('0.00')
    
    # Payment method breakdown
    payment_methods = {}
    for order in orders:
        method = order.payment_method
        payment_methods[method] = payment_methods.get(method, 0) + float(order.get_total_cost)
    for order in manual_orders:
        method = order.payment_method
        payment_methods[method] = payment_methods.get(method, 0) + float(order.get_total_cost)
    
    context = {
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'orders_revenue': orders_revenue,
        'manual_orders_revenue': manual_orders_revenue,
        'aov': aov,
        'payment_methods': payment_methods,
        'date_from': date_from,
        'date_to': date_to,
        'status_filter': status_filter,
        'page_title': 'Sales Overview',
    }
    return render(request, 'sales/sales_overview.html', context)


@login_required
def sales_by_product(request):
    """Sales breakdown by product"""
    # Get filter parameters
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    category_filter = request.GET.get('category', '')
    
    # Get all order items
    order_items = OrderItem.objects.filter(
        order__is_deleted=False,
        order__status__in=['Completed', 'Shipped'],
        order__payment_status='paid'
    )
    manual_order_items = ManualOrderItem.objects.filter(
        order__is_deleted=False,
        order__status__in=['Completed', 'Shipped'],
        order__payment_status='paid'
    )
    
    # Apply date filters
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            order_items = order_items.filter(order__order_date__gte=date_from_obj)
            manual_order_items = manual_order_items.filter(order__order_date__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            order_items = order_items.filter(order__order_date__lte=date_to_obj)
            manual_order_items = manual_order_items.filter(order__order_date__lte=date_to_obj)
        except ValueError:
            pass
    
    # Aggregate by product
    product_sales = {}
    
    for item in order_items:
        product = item.product_variant.product
        if product.product_id not in product_sales:
            product_sales[product.product_id] = {
                'product': product,
                'quantity': 0,
                'revenue': Decimal('0.00'),
                'orders': 0
            }
        product_sales[product.product_id]['quantity'] += item.quantity
        product_sales[product.product_id]['revenue'] += item.item_total
        product_sales[product.product_id]['orders'] += 1
    
    for item in manual_order_items:
        product = item.product_variant.product
        if product.product_id not in product_sales:
            product_sales[product.product_id] = {
                'product': product,
                'quantity': 0,
                'revenue': Decimal('0.00'),
                'orders': 0
            }
        product_sales[product.product_id]['quantity'] += item.quantity
        product_sales[product.product_id]['revenue'] += item.item_total
        product_sales[product.product_id]['orders'] += 1
    
    # Sort by revenue
    sorted_products = sorted(
        product_sales.values(),
        key=lambda x: x['revenue'],
        reverse=True
    )
    
    # Apply category filter if needed
    if category_filter:
        sorted_products = [
            p for p in sorted_products
            if p['product'].category_id == int(category_filter)
        ]
    
    context = {
        'product_sales': sorted_products,
        'date_from': date_from,
        'date_to': date_to,
        'category_filter': category_filter,
        'page_title': 'Sales by Product',
    }
    return render(request, 'sales/sales_by_product.html', context)


@login_required
def sales_by_customer(request):
    """Sales breakdown by customer"""
    # Get filter parameters
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Get all orders
    orders = Order.objects.filter(
        is_deleted=False,
        status__in=['Completed', 'Shipped'],
        payment_status='paid'
    )
    manual_orders = ManualOrder.objects.filter(
        is_deleted=False,
        status__in=['Completed', 'Shipped'],
        payment_status='paid',
        customer__isnull=False
    )
    
    # Apply date filters
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            orders = orders.filter(order_date__gte=date_from_obj)
            manual_orders = manual_orders.filter(order_date__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            orders = orders.filter(order_date__lte=date_to_obj)
            manual_orders = manual_orders.filter(order_date__lte=date_to_obj)
        except ValueError:
            pass
    
    # Aggregate by customer
    customer_sales = {}
    
    for order in orders:
        customer = order.customer
        if customer.id not in customer_sales:
            customer_sales[customer.id] = {
                'customer': customer,
                'total_revenue': Decimal('0.00'),
                'order_count': 0
            }
        customer_sales[customer.id]['total_revenue'] += order.get_total_cost
        customer_sales[customer.id]['order_count'] += 1
    
    for order in manual_orders:
        customer = order.customer
        if customer.id not in customer_sales:
            customer_sales[customer.id] = {
                'customer': customer,
                'total_revenue': Decimal('0.00'),
                'order_count': 0
            }
        customer_sales[customer.id]['total_revenue'] += order.get_total_cost
        customer_sales[customer.id]['order_count'] += 1
    
    # Sort by revenue
    sorted_customers = sorted(
        customer_sales.values(),
        key=lambda x: x['total_revenue'],
        reverse=True
    )
    
    context = {
        'customer_sales': sorted_customers,
        'date_from': date_from,
        'date_to': date_to,
        'page_title': 'Sales by Customer',
    }
    return render(request, 'sales/sales_by_customer.html', context)


@login_required
def sales_trends(request):
    """Sales trends and forecasting"""
    # Get filter parameters
    period = request.GET.get('period', 'month')  # day, week, month, year
    
    # Get all orders
    orders = Order.objects.filter(
        is_deleted=False,
        status__in=['Completed', 'Shipped'],
        payment_status='paid'
    )
    manual_orders = ManualOrder.objects.filter(
        is_deleted=False,
        status__in=['Completed', 'Shipped'],
        payment_status='paid'
    )
    
    # Group by period
    trends = {}
    
    for order in orders:
        if period == 'day':
            key = order.order_date.strftime('%Y-%m-%d')
        elif period == 'week':
            week_start = order.order_date - timedelta(days=order.order_date.weekday())
            key = week_start.strftime('%Y-W%W')
        elif period == 'month':
            key = order.order_date.strftime('%Y-%m')
        else:  # year
            key = order.order_date.strftime('%Y')
        
        if key not in trends:
            trends[key] = {'revenue': Decimal('0.00'), 'orders': 0}
        trends[key]['revenue'] += order.get_total_cost
        trends[key]['orders'] += 1
    
    for order in manual_orders:
        if period == 'day':
            key = order.order_date.strftime('%Y-%m-%d')
        elif period == 'week':
            week_start = order.order_date - timedelta(days=order.order_date.weekday())
            key = week_start.strftime('%Y-W%W')
        elif period == 'month':
            key = order.order_date.strftime('%Y-%m')
        else:  # year
            key = order.order_date.strftime('%Y')
        
        if key not in trends:
            trends[key] = {'revenue': Decimal('0.00'), 'orders': 0}
        trends[key]['revenue'] += order.get_total_cost
        trends[key]['orders'] += 1
    
    # Sort by key
    sorted_trends = sorted(trends.items())
    
    context = {
        'trends': sorted_trends,
        'period': period,
        'page_title': 'Sales Trends',
    }
    return render(request, 'sales/sales_trends.html', context)


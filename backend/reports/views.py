from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import datetime, timedelta
from sales.models import Sale, Shift
from products.models import Purchase

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_report(request):
    """Generate sales report for given time period"""
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if not start_date or not end_date:
        return Response({'error': 'start_date and end_date are required'}, status=400)

    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    # Filter sales by date range and branch
    sales_qs = Sale.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    
    if request.user.branch:
        sales_qs = sales_qs.filter(branch=request.user.branch)

    # Aggregate data
    total_sales = sales_qs.aggregate(
        total_amount=Sum('total_amount'),
        total_cash=Sum('cash_amount'),
        total_mpesa=Sum('mpesa_amount'),
        count=Count('id')
    )

    # Daily breakdown
    daily_sales = []
    current_date = start_date
    while current_date <= end_date:
        day_sales = sales_qs.filter(created_at__date=current_date).aggregate(
            total_amount=Sum('total_amount'),
            total_cash=Sum('cash_amount'),
            total_mpesa=Sum('mpesa_amount'),
            count=Count('id')
        )
        daily_sales.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'total_amount': day_sales['total_amount'] or 0,
            'total_cash': day_sales['total_cash'] or 0,
            'total_mpesa': day_sales['total_mpesa'] or 0,
            'count': day_sales['count'] or 0
        })
        current_date += timedelta(days=1)

    return Response({
        'period': {
            'start_date': start_date.strftime('%Y-%m-%d'),
            'end_date': end_date.strftime('%Y-%m-%d')
        },
        'summary': {
            'total_amount': total_sales['total_amount'] or 0,
            'total_cash': total_sales['total_cash'] or 0,
            'total_mpesa': total_sales['total_mpesa'] or 0,
            'total_transactions': total_sales['count'] or 0
        },
        'daily_breakdown': daily_sales
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def supplier_report(request):
    """Generate supplier invoice report for given time period"""
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if not start_date or not end_date:
        return Response({'error': 'start_date and end_date are required'}, status=400)

    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    # Filter purchases by date range and branch
    purchases_qs = Purchase.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    
    if request.user.branch:
        purchases_qs = purchases_qs.filter(branch=request.user.branch)

    # Group by supplier
    supplier_data = {}
    for purchase in purchases_qs.select_related('supplier'):
        supplier_name = purchase.supplier.name
        if supplier_name not in supplier_data:
            supplier_data[supplier_name] = {
                'supplier_name': supplier_name,
                'total_amount': 0,
                'invoice_count': 0,
                'invoices': []
            }
        
        supplier_data[supplier_name]['total_amount'] += purchase.total_amount
        supplier_data[supplier_name]['invoice_count'] += 1
        supplier_data[supplier_name]['invoices'].append({
            'invoice_number': purchase.invoice_number,
            'amount': purchase.total_amount,
            'date': purchase.created_at.strftime('%Y-%m-%d')
        })

    return Response({
        'period': {
            'start_date': start_date.strftime('%Y-%m-%d'),
            'end_date': end_date.strftime('%Y-%m-%d')
        },
        'suppliers': list(supplier_data.values())
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def shift_variance_report(request):
    """Generate shift variance report"""
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if not start_date or not end_date:
        return Response({'error': 'start_date and end_date are required'}, status=400)

    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)

    # Filter shifts by date range and branch
    shifts_qs = Shift.objects.filter(
        start_time__date__gte=start_date,
        start_time__date__lte=end_date,
        is_closed=True
    )
    
    if request.user.branch:
        shifts_qs = shifts_qs.filter(branch=request.user.branch)

    shifts_data = []
    for shift in shifts_qs.select_related('cashier'):
        shifts_data.append({
            'shift_id': shift.id,
            'cashier': shift.cashier.get_full_name(),
            'date': shift.start_time.strftime('%Y-%m-%d'),
            'expected_cash': shift.calculate_expected_cash(),
            'declared_cash': shift.closing_cash_declared,
            'cash_variance': shift.cash_variance,
            'expected_mpesa': shift.calculate_expected_mpesa(),
            'declared_mpesa': shift.closing_mpesa_declared,
            'mpesa_variance': shift.mpesa_variance,
            'notes': shift.notes
        })

    return Response({
        'period': {
            'start_date': start_date.strftime('%Y-%m-%d'),
            'end_date': end_date.strftime('%Y-%m-%d')
        },
        'shifts': shifts_data
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    """Get dashboard statistics"""
    today = timezone.now().date()
    
    # Today's sales
    today_sales = Sale.objects.filter(
        created_at__date=today,
        branch=request.user.branch if request.user.branch else None
    ).aggregate(
        total_amount=Sum('total_amount'),
        total_cash=Sum('cash_amount'),
        total_mpesa=Sum('mpesa_amount'),
        count=Count('id')
    )

    # This month's sales
    month_start = today.replace(day=1)
    month_sales = Sale.objects.filter(
        created_at__date__gte=month_start,
        branch=request.user.branch if request.user.branch else None
    ).aggregate(
        total_amount=Sum('total_amount'),
        count=Count('id')
    )

    # Active shift info
    active_shift = None
    if request.user.is_active_shift:
        try:
            shift = Shift.objects.get(
                cashier=request.user,
                branch=request.user.branch,
                is_closed=False
            )
            active_shift = {
                'id': shift.id,
                'start_time': shift.start_time,
                'opening_cash': shift.opening_cash,
                'sales_count': shift.sale_set.count()
            }
        except Shift.DoesNotExist:
            pass

    return Response({
        'today_sales': {
            'total_amount': today_sales['total_amount'] or 0,
            'total_cash': today_sales['total_cash'] or 0,
            'total_mpesa': today_sales['total_mpesa'] or 0,
            'count': today_sales['count'] or 0
        },
        'month_sales': {
            'total_amount': month_sales['total_amount'] or 0,
            'count': month_sales['count'] or 0
        },
        'active_shift': active_shift
    })

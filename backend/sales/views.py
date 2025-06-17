from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from .models import Sale, SaleItem, Shift
from .serializers import SaleSerializer, ShiftSerializer
from products.models import Product, Stock, StockMovement
import uuid

class SaleListCreateView(generics.ListCreateAPIView):
    serializer_class = SaleSerializer

    def get_queryset(self):
        queryset = Sale.objects.all()
        if self.request.user.branch:
            queryset = queryset.filter(branch=self.request.user.branch)
        return queryset.order_by('-created_at')

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def process_sale(request):
    """Process a new sale"""
    try:
        with transaction.atomic():
            # Generate receipt number
            receipt_number = f"RCP-{uuid.uuid4().hex[:8].upper()}"

            # Get or create active shift
            shift, created = Shift.objects.get_or_create(
                cashier=request.user,
                branch=request.user.branch,
                is_closed=False,
                defaults={'opening_cash': 0}
            )

            # Create sale
            sale_data = {
                'receipt_number': receipt_number,
                'branch': request.user.branch,
                'cashier': request.user,
                'payment_method': request.data['payment_method'],
                'cash_amount': request.data.get('cash_amount', 0),
                'mpesa_amount': request.data.get('mpesa_amount', 0),
                'total_amount': request.data['total_amount'],
                'discount': request.data.get('discount', 0),
                'customer_name': request.data.get('customer_name', ''),
                'customer_phone': request.data.get('customer_phone', ''),
                'shift': shift
            }
            sale = Sale.objects.create(**sale_data)

            # Process sale items
            for item_data in request.data['items']:
                product = Product.objects.get(id=item_data['product_id'])
                quantity = item_data['quantity']

                # Check stock availability
                try:
                    stock = Stock.objects.get(product=product, branch=request.user.branch)
                    if stock.quantity < quantity:
                        raise Exception(f"Insufficient stock for {product.name}")
                except Stock.DoesNotExist:
                    raise Exception(f"Product {product.name} not available in this branch")

                # Create sale item
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    quantity=quantity,
                    unit_price=item_data['unit_price'],
                    discount=item_data.get('discount', 0)
                )

                # Update stock
                stock.quantity -= quantity
                stock.save()

                # Create stock movement
                StockMovement.objects.create(
                    product=product,
                    branch=request.user.branch,
                    movement_type='sale',
                    quantity=-quantity,
                    reference=receipt_number,
                    created_by=request.user
                )

        return Response({
            'message': 'Sale processed successfully',
            'receipt_number': receipt_number,
            'sale_id': sale.id
        })
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_shift(request):
    """Start a new shift"""
    # Check if user has an active shift
    active_shift = Shift.objects.filter(
        cashier=request.user,
        branch=request.user.branch,
        is_closed=False
    ).first()

    if active_shift:
        return Response({'error': 'You already have an active shift'}, 
                       status=status.HTTP_400_BAD_REQUEST)

    shift = Shift.objects.create(
        cashier=request.user,
        branch=request.user.branch,
        opening_cash=request.data.get('opening_cash', 0)
    )

    request.user.is_active_shift = True
    request.user.save()

    return Response({
        'message': 'Shift started successfully',
        'shift_id': shift.id
    })

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def close_shift(request):
    """Close current shift"""
    try:
        shift = Shift.objects.get(
            cashier=request.user,
            branch=request.user.branch,
            is_closed=False
        )
    except Shift.DoesNotExist:
        return Response({'error': 'No active shift found'}, 
                       status=status.HTTP_400_BAD_REQUEST)

    shift.closing_cash_declared = request.data['closing_cash']
    shift.closing_mpesa_declared = request.data['closing_mpesa']
    shift.notes = request.data.get('notes', '')
    shift.end_time = timezone.now()
    shift.is_closed = True

    # Calculate variances
    shift.calculate_variances()
    shift.save()

    request.user.is_active_shift = False
    request.user.save()

    return Response({
        'message': 'Shift closed successfully',
        'cash_variance': shift.cash_variance,
        'mpesa_variance': shift.mpesa_variance
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current_shift(request):
    """Get current active shift"""
    try:
        shift = Shift.objects.get(
            cashier=request.user,
            branch=request.user.branch,
            is_closed=False
        )
        serializer = ShiftSerializer(shift)
        return Response(serializer.data)
    except Shift.DoesNotExist:
        return Response({'error': 'No active shift'}, status=status.HTTP_404_NOT_FOUND)

class ShiftListView(generics.ListAPIView):
    serializer_class = ShiftSerializer

    def get_queryset(self):
        queryset = Shift.objects.all()
        if self.request.user.branch:
            queryset = queryset.filter(branch=self.request.user.branch)
        return queryset.order_by('-start_time')

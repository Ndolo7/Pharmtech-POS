from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from .models import (Category, Supplier, Product, Stock, StockMovement, 
                    Purchase, PurchaseItem, Transfer, TransferItem)
from .serializers import (CategorySerializer, SupplierSerializer, ProductSerializer, 
                         StockSerializer, StockMovementSerializer, PurchaseSerializer, 
                         TransferSerializer)

class CategoryListCreateView(generics.ListCreateAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

class SupplierListCreateView(generics.ListCreateAPIView):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer

class ProductListCreateView(generics.ListCreateAPIView):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer

class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

class StockListView(generics.ListAPIView):
    serializer_class = StockSerializer

    def get_queryset(self):
        queryset = Stock.objects.all()
        branch_id = self.request.query_params.get('branch', None)
        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)
        elif self.request.user.branch:
            queryset = queryset.filter(branch=self.request.user.branch)
        return queryset

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def receive_stock(request):
    """Receive stock from supplier"""
    try:
        with transaction.atomic():
            # Create purchase record
            purchase_data = {
                'supplier_id': request.data['supplier_id'],
                'branch': request.user.branch,
                'invoice_number': request.data['invoice_number'],
                'total_amount': request.data['total_amount'],
                'created_by': request.user
            }
            purchase = Purchase.objects.create(**purchase_data)

            # Process each item
            for item_data in request.data['items']:
                product = Product.objects.get(id=item_data['product_id'])
                
                # Create purchase item
                PurchaseItem.objects.create(
                    purchase=purchase,
                    product=product,
                    quantity=item_data['quantity'],
                    unit_cost=item_data['unit_cost']
                )

                # Update stock
                stock, created = Stock.objects.get_or_create(
                    product=product,
                    branch=request.user.branch,
                    defaults={'quantity': 0}
                )
                stock.quantity += item_data['quantity']
                stock.save()

                # Create stock movement
                StockMovement.objects.create(
                    product=product,
                    branch=request.user.branch,
                    movement_type='purchase',
                    quantity=item_data['quantity'],
                    reference=purchase.invoice_number,
                    created_by=request.user
                )

        return Response({'message': 'Stock received successfully'})
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def transfer_stock(request):
    """Transfer stock between branches"""
    try:
        with transaction.atomic():
            transfer = Transfer.objects.create(
                from_branch=request.user.branch,
                to_branch_id=request.data['to_branch_id'],
                created_by=request.user,
                notes=request.data.get('notes', '')
            )

            for item_data in request.data['items']:
                product = Product.objects.get(id=item_data['product_id'])
                quantity = item_data['quantity']

                # Check stock availability
                from_stock = Stock.objects.get(product=product, branch=request.user.branch)
                if from_stock.quantity < quantity:
                    raise Exception(f"Insufficient stock for {product.name}")

                # Create transfer item
                TransferItem.objects.create(
                    transfer=transfer,
                    product=product,
                    quantity=quantity
                )

                # Update from branch stock
                from_stock.quantity -= quantity
                from_stock.save()

                # Update to branch stock
                to_stock, created = Stock.objects.get_or_create(
                    product=product,
                    branch_id=request.data['to_branch_id'],
                    defaults={'quantity': 0}
                )
                to_stock.quantity += quantity
                to_stock.save()

                # Create stock movements
                StockMovement.objects.create(
                    product=product,
                    branch=request.user.branch,
                    movement_type='transfer_out',
                    quantity=-quantity,
                    reference=f"Transfer #{transfer.id}",
                    created_by=request.user
                )

                StockMovement.objects.create(
                    product=product,
                    branch_id=request.data['to_branch_id'],
                    movement_type='transfer_in',
                    quantity=quantity,
                    reference=f"Transfer #{transfer.id}",
                    created_by=request.user
                )

        return Response({'message': 'Stock transferred successfully'})
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def adjust_stock(request):
    """Adjust stock quantities (Super Admin only)"""
    if not request.user.can_adjust_stock():
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    try:
        with transaction.atomic():
            product = Product.objects.get(id=request.data['product_id'])
            new_quantity = request.data['new_quantity']
            reason = request.data.get('reason', '')

            stock, created = Stock.objects.get_or_create(
                product=product,
                branch=request.user.branch,
                defaults={'quantity': 0}
            )

            old_quantity = stock.quantity
            adjustment = new_quantity - old_quantity
            stock.quantity = new_quantity
            stock.save()

            # Create stock movement
            StockMovement.objects.create(
                product=product,
                branch=request.user.branch,
                movement_type='adjustment',
                quantity=adjustment,
                notes=reason,
                created_by=request.user
            )

        return Response({'message': 'Stock adjusted successfully'})
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

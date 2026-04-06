from rest_framework import serializers
from .models import (Category, Supplier, Product, Stock, StockMovement, 
                    Purchase, PurchaseItem, Transfer, TransferItem)

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'

class StockSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stock
        fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
    current_stock = serializers.SerializerMethodField()
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Product
        fields = '__all__'

    def get_current_stock(self, obj):
        request = self.context.get('request')
        if request and hasattr(request.user, 'branch') and request.user.branch:
            return obj.current_stock(request.user.branch)
        return obj.current_stock()

class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    class Meta:
        model = StockMovement
        fields = '__all__'

class PurchaseItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = PurchaseItem
        fields = '__all__'

class PurchaseSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)

    class Meta:
        model = Purchase
        fields = '__all__'

class TransferItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = TransferItem
        fields = '__all__'

class TransferSerializer(serializers.ModelSerializer):
    items = TransferItemSerializer(many=True, read_only=True)
    from_branch_name = serializers.CharField(source='from_branch.name', read_only=True)
    to_branch_name = serializers.CharField(source='to_branch.name', read_only=True)

    class Meta:
        model = Transfer
        fields = '__all__'

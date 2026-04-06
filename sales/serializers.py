from rest_framework import serializers
from .models import Sale, SaleItem, Shift

class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = SaleItem
        fields = '__all__'

class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    cashier_name = serializers.CharField(source='cashier.get_full_name', read_only=True)

    class Meta:
        model = Sale
        fields = '__all__'

class ShiftSerializer(serializers.ModelSerializer):
    cashier_name = serializers.CharField(source='cashier.get_full_name', read_only=True)
    expected_cash = serializers.SerializerMethodField()
    expected_mpesa = serializers.SerializerMethodField()
    total_sales = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = '__all__'

    def get_expected_cash(self, obj):
        return obj.calculate_expected_cash()

    def get_expected_mpesa(self, obj):
        return obj.calculate_expected_mpesa()

    def get_total_sales(self, obj):
        return obj.sale_set.count()

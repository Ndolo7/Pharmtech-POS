from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class Sale(models.Model):
    PAYMENT_METHODS = [
        ('cash', 'Cash'),
        ('mpesa', 'M-Pesa'),
        ('mixed', 'Mixed'),
    ]

    receipt_number = models.CharField(max_length=50, unique=True)
    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE)
    cashier = models.ForeignKey(User, on_delete=models.CASCADE)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHODS)
    cash_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    mpesa_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=15, blank=True)
    shift = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Sale {self.receipt_number} - KES {self.total_amount}"

class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.total_price = (self.quantity * self.unit_price) - self.discount
        super().save(*args, **kwargs)

class Shift(models.Model):
    cashier = models.ForeignKey(User, on_delete=models.CASCADE)
    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    opening_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closing_cash_declared = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    closing_mpesa_declared = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cash_variance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    mpesa_variance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_closed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Shift {self.id} - {self.cashier.username} ({self.start_time.date()})"

    def calculate_expected_cash(self):
        sales = self.sale_set.all()
        return sum(sale.cash_amount for sale in sales) + self.opening_cash

    def calculate_expected_mpesa(self):
        sales = self.sale_set.all()
        return sum(sale.mpesa_amount for sale in sales)

    def calculate_variances(self):
        if self.closing_cash_declared is not None:
            expected_cash = self.calculate_expected_cash()
            self.cash_variance = self.closing_cash_declared - expected_cash

        if self.closing_mpesa_declared is not None:
            expected_mpesa = self.calculate_expected_mpesa()
            self.mpesa_variance = self.closing_mpesa_declared - expected_mpesa

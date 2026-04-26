from decimal import Decimal

from django.db import models
from django.contrib.auth import get_user_model
from django.db.models import Sum

User = get_user_model()

class Sale(models.Model):
    PAYMENT_METHODS = [
        ('cash', 'Cash'),
        ('mpesa', 'M-Pesa'),
        ('credit', 'Credit'),
        ('mixed', 'Mixed'),
    ]

    receipt_number = models.CharField(max_length=50, unique=True)
    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE)
    cashier = models.ForeignKey(User, on_delete=models.CASCADE)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHODS)
    cash_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    mpesa_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
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


class CreditAccount(models.Model):
    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, related_name='credit_accounts')
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=15, blank=True)
    outstanding_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-outstanding_balance", "customer_name")

    def __str__(self):
        identity = self.customer_phone or self.customer_name
        return f"{identity} - KES {self.outstanding_balance}"


class CreditTransaction(models.Model):
    TYPE_CHARGE = "charge"
    TYPE_REPAYMENT = "repayment"
    TYPE_ADJUSTMENT = "adjustment"
    TRANSACTION_TYPES = [
        (TYPE_CHARGE, "Credit Sale"),
        (TYPE_REPAYMENT, "Repayment"),
        (TYPE_ADJUSTMENT, "Adjustment"),
    ]

    PAYMENT_METHOD_CASH = "cash"
    PAYMENT_METHOD_MPESA = "mpesa"
    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_METHOD_CASH, "Cash"),
        (PAYMENT_METHOD_MPESA, "M-Pesa"),
    ]

    account = models.ForeignKey(CreditAccount, on_delete=models.CASCADE, related_name="transactions")
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name="credit_transactions")
    branch = models.ForeignKey('branches.Branch', on_delete=models.CASCADE, related_name="credit_transactions")
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.get_transaction_type_display()} - KES {self.amount}"

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
        sales_cash_total = sum((sale.cash_amount for sale in sales), Decimal("0"))
        expenses_total = self.expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return (sales_cash_total + self.opening_cash) - expenses_total

    def calculate_expected_mpesa(self):
        sales = self.sale_set.all()
        return sum((sale.mpesa_amount for sale in sales), Decimal("0"))

    def calculate_variances(self):
        if self.closing_cash_declared is not None:
            expected_cash = self.calculate_expected_cash()
            self.cash_variance = self.closing_cash_declared - expected_cash

        if self.closing_mpesa_declared is not None:
            expected_mpesa = self.calculate_expected_mpesa()
            self.mpesa_variance = self.closing_mpesa_declared - expected_mpesa


class ShiftExpense(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="expenses")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.description} - KES {self.amount}"

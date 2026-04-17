import uuid

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Categories"


class Supplier(models.Model):
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=15)
    email = models.EmailField()
    address = models.TextField()
    priority = models.PositiveIntegerField(default=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=200)
    barcode = models.CharField(max_length=50, unique=True, blank=True)
    description = models.TextField(blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    reorder_level = models.IntegerField(default=10)
    max_stock = models.PositiveIntegerField(default=100)
    pack_quantity = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def current_stock(self, branch=None):
        if branch:
            return self.stock_set.filter(branch=branch).aggregate(total=models.Sum("quantity"))["total"] or 0
        return self.stock_set.aggregate(total=models.Sum("quantity"))["total"] or 0


class AutoReorderRequest(models.Model):
    ORIGIN_AUTO = "auto"
    ORIGIN_MANUAL = "manual"

    ORIGIN_CHOICES = [
        (ORIGIN_AUTO, "Automatic"),
        (ORIGIN_MANUAL, "Manual"),
    ]

    STATUS_OPEN = "open"
    STATUS_FULFILLED = "fulfilled"
    STATUS_EXHAUSTED = "exhausted"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_FULFILLED, "Fulfilled"),
        (STATUS_EXHAUSTED, "Exhausted Suppliers"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="auto_reorder_requests")
    target_stock_level = models.PositiveIntegerField()
    current_stock_snapshot = models.IntegerField()
    requested_quantity = models.PositiveIntegerField()
    remaining_quantity = models.PositiveIntegerField()
    origin = models.CharField(max_length=12, choices=ORIGIN_CHOICES, default=ORIGIN_AUTO)
    branch_requirements = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    admin_notified = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reorder #{self.pk} {self.product.name} ({self.status})"


class SupplierReorderRequest(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_PARTIAL = "partial"
    STATUS_REJECTED = "rejected"
    STATUS_EXPIRED = "expired"
    STATUS_EMAIL_FAILED = "email_failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_EMAIL_FAILED, "Email Failed"),
    ]

    reorder_request = models.ForeignKey(
        AutoReorderRequest,
        on_delete=models.CASCADE,
        related_name="supplier_requests",
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="reorder_requests")
    priority = models.PositiveIntegerField()
    requested_quantity = models.PositiveIntegerField()
    fulfilled_quantity = models.PositiveIntegerField(default=0)
    received_quantity = models.PositiveIntegerField(default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    emailed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Supplier Request #{self.pk} {self.supplier.name} ({self.status})"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def pending_quantity(self):
        if self.fulfilled_quantity > 0:
            return max(0, self.fulfilled_quantity - self.received_quantity)
        return 0


class Stock(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    branch = models.ForeignKey("branches.Branch", on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["product", "branch"]

    def __str__(self):
        return f"{self.product.name} - {self.branch.name}: {self.quantity}"


class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ("purchase", "Purchase"),
        ("sale", "Sale"),
        ("transfer_in", "Transfer In"),
        ("transfer_out", "Transfer Out"),
        ("adjustment", "Adjustment"),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    branch = models.ForeignKey("branches.Branch", on_delete=models.CASCADE)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - {self.movement_type}: {self.quantity}"


class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    branch = models.ForeignKey("branches.Branch", on_delete=models.CASCADE)
    invoice_number = models.CharField(max_length=100)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Purchase {self.invoice_number} - {self.supplier.name}"


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        self.total_cost = self.quantity * self.unit_cost
        super().save(*args, **kwargs)


class Transfer(models.Model):
    from_branch = models.ForeignKey("branches.Branch", on_delete=models.CASCADE, related_name="transfers_out")
    to_branch = models.ForeignKey("branches.Branch", on_delete=models.CASCADE, related_name="transfers_in")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Transfer from {self.from_branch.name} to {self.to_branch.name}"


class TransferItem(models.Model):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField()

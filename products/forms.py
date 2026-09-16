from django import forms

from branches.models import Branch

from .models import Category, Product, Supplier
from .sms import normalize_phone_number


class ProductForm(forms.ModelForm):
    stock_quantity = forms.IntegerField(
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-input", "placeholder": "0", "min": "0"}),
    )

    class Meta:
        model = Product
        fields = (
            "name",
            "barcode",
            "description",
            "unit_price",
            "cost_price",
            "reorder_level",
            "max_stock",
            "pack_quantity",
            "exempt_from_auto_reorder",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Product name"}),
            "barcode": forms.TextInput(attrs={"class": "form-input", "placeholder": "Barcode (optional)"}),
            "description": forms.Textarea(attrs={"class": "form-textarea", "rows": 2, "placeholder": "Description"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
            "cost_price": forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
            "reorder_level": forms.NumberInput(attrs={"class": "form-input", "placeholder": "10"}),
            "max_stock": forms.NumberInput(attrs={"class": "form-input", "placeholder": "100", "min": "1"}),
            "pack_quantity": forms.NumberInput(attrs={"class": "form-input", "placeholder": "1", "min": "1"}),
            "exempt_from_auto_reorder": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }



class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input"}),
            "description": forms.Textarea(attrs={"class": "form-textarea", "rows": 2}),
        }


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ("name", "branch", "contact_person", "phone_number", "email", "address")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input"}),
            "branch": forms.Select(attrs={"class": "form-input search-select"}),
            "contact_person": forms.TextInput(attrs={"class": "form-input"}),
            "phone_number": forms.TextInput(attrs={"class": "form-input", "placeholder": "07XXXXXXXX or 2547XXXXXXXX"}),
            "email": forms.EmailInput(attrs={"class": "form-input", "required": True}),
            "address": forms.Textarea(attrs={"class": "form-textarea", "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = Branch.objects.filter(is_active=True).order_by("name")
        self.fields["branch"].required = False
        self.fields["branch"].empty_label = "Global supplier"

    def clean_phone_number(self):
        raw_phone = self.cleaned_data.get("phone_number")
        normalized = normalize_phone_number(raw_phone)
        if not normalized:
            raise forms.ValidationError("Enter a valid Kenyan mobile number.")
        return normalized


class SupplierReorderResponseForm(forms.Form):
    CAN_SUPPLY_CHOICES = (("yes", "Yes"), ("no", "No"))

    can_supply = forms.ChoiceField(
        choices=CAN_SUPPLY_CHOICES,
        initial="yes",
        widget=forms.RadioSelect,
    )
    quantity = forms.IntegerField(min_value=0, required=False)
    def __init__(self, *args, max_quantity=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_quantity = max(0, int(max_quantity or 0))
        self.fields["quantity"].widget.attrs.update(
            {
                "class": "form-input",
                "placeholder": f"Max {self.max_quantity}",
                "max": str(self.max_quantity),
                "min": "0",
            }
        )

    def clean(self):
        cleaned = super().clean()
        can_supply = cleaned.get("can_supply")
        quantity = cleaned.get("quantity")

        if can_supply == "yes":
            if quantity is None:
                self.add_error("quantity", "Enter the quantity you can supply.")
            elif quantity <= 0:
                self.add_error("quantity", "Quantity must be greater than zero.")
            elif quantity > self.max_quantity:
                self.add_error("quantity", f"Quantity cannot exceed {self.max_quantity}.")
        else:
            cleaned["quantity"] = 0

        return cleaned


class AdjustStockForm(forms.Form):
    new_quantity = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-input", "placeholder": "New quantity"}),
    )


class ReceiveStockForm(forms.Form):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        widget=forms.Select(attrs={"class": "form-input"}),
    )
    invoice_number = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "INV-001"}),
    )


class TransferStockForm(forms.Form):
    to_branch = forms.ModelChoiceField(
        queryset=Branch.objects.filter(is_active=True),
        widget=forms.Select(attrs={"class": "form-input"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-textarea", "rows": 2, "placeholder": "Optional notes"}),
    )


class ProductBulkUploadForm(forms.Form):
    file = forms.FileField(
        widget=forms.FileInput(attrs={"class": "form-input", "accept": ".xlsx"}),
        help_text="Upload an .xlsx file with product rows.",
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if not uploaded.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Please upload an .xlsx file.")
        return uploaded

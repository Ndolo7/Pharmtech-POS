from django import forms
from .models import Product, Category, Supplier
from branches.models import Branch


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ("name", "barcode", "category", "description", "unit_price", "cost_price", "reorder_level")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Product name"}),
            "barcode": forms.TextInput(attrs={"class": "form-input", "placeholder": "Barcode (optional)"}),
            "category": forms.Select(attrs={"class": "form-input"}),
            "description": forms.Textarea(attrs={"class": "form-textarea", "rows": 2, "placeholder": "Description"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
            "cost_price": forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
            "reorder_level": forms.NumberInput(attrs={"class": "form-input", "placeholder": "10"}),
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
        fields = ("name", "contact_person", "phone_number", "email", "address")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input"}),
            "contact_person": forms.TextInput(attrs={"class": "form-input"}),
            "phone_number": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input", "required": True}),
            "address": forms.Textarea(attrs={"class": "form-textarea", "rows": 2}),
        }


class AdjustStockForm(forms.Form):
    new_quantity = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-input", "placeholder": "New quantity"}),
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-textarea", "rows": 2, "placeholder": "Reason for adjustment"}),
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

from django import forms


class ShiftForm(forms.Form):
    opening_cash = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        initial=0,
        widget=forms.NumberInput(attrs={
            "class": "form-input",
            "placeholder": "0.00",
            "step": "0.01",
        }),
    )


class StartShiftForm(ShiftForm):
    """Backward-compatible alias used by existing views/templates."""


class CloseShiftForm(forms.Form):
    closing_cash = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
        label="Closing Cash (KES)",
    )
    closing_mpesa = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        initial=0,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
        label="Closing M-Pesa (KES)",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-textarea", "rows": 2, "placeholder": "Optional notes"}),
    )


class SaleForm(forms.Form):
    payment_method = forms.ChoiceField(
        choices=(("cash", "Cash"), ("mpesa", "M-Pesa"), ("credit", "Credit"), ("mixed", "Mixed")),
        initial="cash",
    )
    cash_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False)
    mpesa_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False)
    credit_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False)
    customer_name = forms.CharField(max_length=200, required=False)
    customer_phone = forms.CharField(max_length=15, required=False)
    cart_json = forms.CharField(widget=forms.HiddenInput())


class CreditRepaymentForm(forms.Form):
    account_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    amount = forms.DecimalField(
        min_value=0.01,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
        label="Repayment Amount (KES)",
    )
    payment_method = forms.ChoiceField(
        choices=(("cash", "Cash"), ("mpesa", "M-Pesa")),
        widget=forms.Select(attrs={"class": "form-input"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-textarea", "rows": 2, "placeholder": "Optional note"}),
    )

from django import forms
from accounts.models import User
from .models import Branch


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ("name", "code", "address", "phone_number", "manager", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Branch name"}),
            "code": forms.TextInput(attrs={"class": "form-input", "placeholder": "Code"}),
            "address": forms.Textarea(attrs={"class": "form-textarea", "rows": 2, "placeholder": "Address"}),
            "phone_number": forms.TextInput(attrs={"class": "form-input", "placeholder": "Phone"}),
            "manager": forms.Select(attrs={"class": "form-input"}),
            "is_active": forms.CheckboxInput(attrs={"style": "margin-right:8px"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = User.objects.filter(is_active=True).order_by("username")
        self.fields["manager"].required = False

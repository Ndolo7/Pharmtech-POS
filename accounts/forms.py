from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from .models import User


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-input",
            "placeholder": "Username",
            "autofocus": True,
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-input",
            "placeholder": "Password",
        })
    )


class UserCreateForm(UserCreationForm):
    first_name = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "First Name"}),
    )
    last_name = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "Last Name"}),
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-input", "placeholder": "Email"}),
    )
    phone_number = forms.CharField(
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "Phone Number"}),
    )
    role = forms.ChoiceField(
        choices=User.ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "form-input"}),
    )
    branch = forms.ModelChoiceField(
        queryset=None,
        required=False,
        widget=forms.Select(attrs={"class": "form-input"}),
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "phone_number", "role", "branch", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from branches.models import Branch
        self.fields["branch"].queryset = Branch.objects.filter(is_active=True)
        for field_name in ["username", "password1", "password2"]:
            self.fields[field_name].widget.attrs["class"] = "form-input"


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone_number", "role", "branch", "is_active")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-input"}),
            "last_name": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "phone_number": forms.TextInput(attrs={"class": "form-input"}),
            "role": forms.Select(attrs={"class": "form-input"}),
            "branch": forms.Select(attrs={"class": "form-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from branches.models import Branch
        self.fields["branch"].queryset = Branch.objects.filter(is_active=True)
        self.fields["branch"].required = False

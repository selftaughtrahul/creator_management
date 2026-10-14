from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from accounts.models import DEFAULT_EMPLOYEE_PERMISSIONS, PERMISSION_CHOICES, StudioProfile

User = get_user_model()


class NameSignupForm(forms.Form):
    """Mixed into allauth's signup form. Email still has to be verified."""

    field_order = ["full_name", "email", "password1", "password2"]
    full_name = forms.CharField(
        max_length=150,
        label="Full name",
        widget=forms.TextInput(
            attrs={"placeholder": "Your name", "autocomplete": "name"}
        ),
    )

    def signup(self, request, user):
        name = self.cleaned_data.get("full_name", "").strip()
        first, _, last = name.partition(" ")
        user.first_name = first
        user.last_name = last
        user.save(update_fields=["first_name", "last_name"])


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name"]
        widgets = {
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
        }


class EmployeeJoinForm(forms.Form):
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": "form-control"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": "form-control"}))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={"class": "form-control"}), label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={"class": "form-control"}), label="Confirm password")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        data = super().clean()
        if data.get("password1") and data.get("password1") != data.get("password2"):
            self.add_error("password2", "Passwords do not match.")
        if data.get("password1"):
            validate_password(data["password1"])
        return data


class PeopleForm(forms.Form):
    role = forms.ChoiceField(
        choices=[
            (StudioProfile.Role.ADMIN, "Admin"),
            (StudioProfile.Role.EMPLOYEE, "Employee"),
            (StudioProfile.Role.CREATOR, "Creator"),
        ]
    )
    permissions = forms.MultipleChoiceField(
        choices=PERMISSION_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Used for employees. Admins always have every permission.",
    )
    is_active = forms.BooleanField(required=False, initial=True, label="Account active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.initial.get("permissions"):
            self.initial["permissions"] = list(DEFAULT_EMPLOYEE_PERMISSIONS)

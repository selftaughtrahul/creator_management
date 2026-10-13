from django import forms
from django.contrib.auth import get_user_model

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

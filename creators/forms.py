from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from creators.models import Category, CreatorDocument, CreatorProfile, Subcategory

User = get_user_model()


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


def _style(form):
    for field in form.fields.values():
        if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
            continue
        if isinstance(field.widget, forms.Select):
            field.widget.attrs.setdefault("class", "form-select")
        else:
            field.widget.attrs.setdefault("class", "form-control")


class CreatorJoinForm(StyledForm):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")
    location = forms.CharField(max_length=120, required=False)
    discoverable = forms.BooleanField(
        required=False,
        initial=True,
        label="Allow teams to discover my profile when I share metrics",
    )
    share_metrics = forms.BooleanField(
        required=False,
        initial=True,
        label="Share stored social metrics with authorized team searches",
    )

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


class CreatorProfileForm(StyledModelForm):
    class Meta:
        model = CreatorProfile
        fields = [
            "display_name",
            "bio",
            "location",
            "audience_notes",
            "niches",
            "rate_amount",
            "rate_currency",
            "share_metrics",
            "discoverable",
        ]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4}),
            "audience_notes": forms.Textarea(attrs={"rows": 3}),
            "niches": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["niches"].queryset = Subcategory.objects.select_related("category")
        self.fields["discoverable"].help_text = "Requires metric sharing. Opt-in only."
        self.fields["share_metrics"].help_text = "Official APIs still only expose data from accounts you connect."

    def clean(self):
        data = super().clean()
        if data.get("discoverable") and not data.get("share_metrics"):
            self.add_error("discoverable", "Discovery requires sharing stored metrics.")
        return data


class CreatorDocumentForm(StyledModelForm):
    class Meta:
        model = CreatorDocument
        fields = ["title", "file"]


class ClassificationReviewForm(StyledForm):
    category = forms.ModelChoiceField(queryset=Category.objects.all())
    subcategory = forms.ModelChoiceField(queryset=Subcategory.objects.select_related("category"), required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    verified_label = forms.BooleanField(required=False, initial=True, label="Save as a verified evaluation label")

    def clean(self):
        data = super().clean()
        subcategory = data.get("subcategory")
        category = data.get("category")
        if subcategory and category and subcategory.category_id != category.id:
            self.add_error("subcategory", "Subcategory must belong to the selected category.")
        return data


class EnrichmentRequestForm(StyledForm):
    platform = forms.ChoiceField(choices=[])

    def __init__(self, *args, platforms=None, **kwargs):
        super().__init__(*args, **kwargs)
        from networks.models import SocialAccount

        self.fields["platform"].choices = platforms or SocialAccount.Platform.choices

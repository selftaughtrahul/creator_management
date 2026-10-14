from django import forms

from campaigns.models import Campaign, Invitation, MessageTemplate, Offer, RecommendationFeedback
from creators.forms import StyledForm, StyledModelForm
from creators.models import Category, Subcategory
from networks.models import SocialAccount


class CampaignForm(StyledModelForm):
    platforms = forms.MultipleChoiceField(
        choices=SocialAccount.Platform.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Campaign
        fields = [
            "name",
            "objective",
            "budget",
            "niche",
            "subcategory",
            "location",
            "platforms",
            "follower_min",
            "follower_max",
            "min_engagement",
            "audience_notes",
            "status",
        ]
        widgets = {
            "objective": forms.Textarea(attrs={"rows": 3}),
            "audience_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["niche"].queryset = Category.objects.all()
        self.fields["subcategory"].queryset = Subcategory.objects.select_related("category")
        if self.instance and self.instance.pk:
            self.initial["platforms"] = self.instance.platforms or []

    def clean(self):
        data = super().clean()
        low = data.get("follower_min")
        high = data.get("follower_max")
        if low is not None and high is not None and low > high:
            self.add_error("follower_max", "Maximum followers must be at least the minimum.")
        subcategory = data.get("subcategory")
        niche = data.get("niche")
        if subcategory and niche and subcategory.category_id != niche.id:
            self.add_error("subcategory", "Subcategory must belong to the selected niche.")
        return data

    def save(self, commit=True):
        campaign = super().save(commit=False)
        campaign.platforms = self.cleaned_data.get("platforms") or []
        if commit:
            campaign.save()
        return campaign


class DiscoverForm(StyledForm):
    query = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Find gaming creators in India with 50K–500K followers…"}),
        required=False,
    )
    location = forms.CharField(required=False)
    niche = forms.ModelChoiceField(queryset=Category.objects.all(), required=False)
    platform = forms.ChoiceField(choices=[("", "Any")] + list(SocialAccount.Platform.choices), required=False)
    sort = forms.ChoiceField(
        choices=[
            ("relevance", "Relevance"),
            ("followers", "Followers"),
            ("engagement", "Engagement"),
            ("synced", "Last synced"),
            ("name", "Name"),
        ],
        required=False,
        initial="relevance",
    )


class FeedbackForm(StyledForm):
    decision = forms.ChoiceField(choices=RecommendationFeedback.Decision.choices, widget=forms.RadioSelect)
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class TemplateForm(StyledModelForm):
    class Meta:
        model = MessageTemplate
        fields = ["name", "subject", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 8})}
        help_texts = {
            "body": "Placeholders: {creator_name}, {campaign_name}, {location}",
        }


class OutreachForm(StyledForm):
    template = forms.ModelChoiceField(queryset=MessageTemplate.objects.all())
    creator_ids = forms.CharField(widget=forms.HiddenInput)
    follow_up_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))

    def clean_creator_ids(self):
        raw = self.cleaned_data["creator_ids"]
        ids = [int(part) for part in raw.split(",") if part.strip().isdigit()]
        if not ids:
            raise forms.ValidationError("Select at least one creator.")
        return ids


class InvitationOutcomeForm(StyledForm):
    status = forms.ChoiceField(
        choices=[
            (Invitation.Status.RESPONDED, "Responded"),
            (Invitation.Status.ACCEPTED, "Accepted"),
            (Invitation.Status.REJECTED, "Rejected"),
        ]
    )
    note = forms.CharField(required=False, max_length=255)


class OfferForm(StyledModelForm):
    class Meta:
        model = Offer
        fields = ["amount", "currency", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class CounterOfferForm(StyledForm):
    amount = forms.DecimalField(max_digits=12, decimal_places=2)
    currency = forms.CharField(max_length=8, initial="INR")
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class MembershipCostForm(StyledForm):
    recorded_cost = forms.DecimalField(max_digits=12, decimal_places=2, required=False)

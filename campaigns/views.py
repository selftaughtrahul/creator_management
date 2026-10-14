from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.notify import notify_creator_offer
from accounts.permissions import permission_required, superuser_required
from campaigns.dashboard import studio_dashboard
from campaigns.export import shortlist_csv
from campaigns.forms import (
    CampaignForm,
    DiscoverForm,
    FeedbackForm,
    InvitationOutcomeForm,
    MembershipCostForm,
    OfferForm,
    OutreachForm,
    TemplateForm,
)
from campaigns.match import match_campaign
from campaigns.models import (
    Campaign,
    CampaignCreator,
    CampaignDocument,
    Invitation,
    MessageTemplate,
    Offer,
    RecommendationFeedback,
)
from campaigns.outreach import record_outcome, send_invitation
from campaigns.status import apply_offer_response
from campaigns.sample_data import delete_studio_sample, load_studio_sample
from campaigns.search import search_creators
from creators.jobs import enqueue_job
from creators.metrics import creator_metrics
from creators.models import CreatorProfile
from creators.parser import criteria_from_campaign
from networks.models import SocialAccount


@permission_required("studio_dashboard")
def dashboard(request):
    return render(request, "campaigns/dashboard.html", studio_dashboard(request))


@superuser_required
@require_POST
def load_sample_data(request):
    loaded = load_studio_sample(request.user)
    messages.success(
        request,
        f"Sample data loaded: {loaded['creators']} creators, {loaded['campaigns']} campaigns, and sample metrics on every network.",
    )
    return redirect("campaigns:dashboard")


@superuser_required
@require_POST
def delete_sample_data(request):
    delete_studio_sample()
    messages.success(request, "Sample creators, metrics, and the sample campaign were deleted.")
    return redirect("campaigns:dashboard")


def _campaign_list(request, form=None, open_form=False, editing=None):
    rows = Campaign.objects.select_related("owner", "niche")
    status = request.GET.get("status")
    if status:
        rows = rows.filter(status=status)
    page = Paginator(rows, 12).get_page(request.GET.get("page"))
    return render(
        request,
        "campaigns/list.html",
        {
            "page": page,
            "status": status,
            "form": form or CampaignForm(),
            "open_form": open_form,
            "editing": editing,
        },
    )


@permission_required("campaigns")
def campaign_list(request):
    return _campaign_list(request, open_form=request.GET.get("new") == "1")


@permission_required("campaigns")
def campaign_create(request):
    form = CampaignForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.owner = request.user
        campaign.save()
        _store_documents(campaign, form.cleaned_data.get("documents") or [], request.user)
        messages.success(request, "Campaign saved.")
        return redirect("campaigns:detail", pk=campaign.pk)
    return _campaign_list(request, form=form, open_form=True)


@permission_required("campaigns")
def campaign_edit(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    form = CampaignForm(request.POST or None, request.FILES or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        form.save()
        _store_documents(campaign, form.cleaned_data.get("documents") or [], request.user)
        messages.success(request, "Campaign updated.")
        return redirect("campaigns:detail", pk=campaign.pk)
    return _campaign_list(request, form=form, open_form=True, editing=campaign)


def _store_documents(campaign, files, user):
    for uploaded in files:
        CampaignDocument.objects.create(
            campaign=campaign,
            title=(uploaded.name or "Document")[:180],
            file=uploaded,
            size=uploaded.size,
            uploaded_by=user,
        )


def _render_campaign_detail(request, campaign, *, edit_form=None, open_drawer=""):
    memberships = campaign.memberships.select_related("creator")
    rows = []
    for membership in memberships:
        match = match_campaign(campaign, membership.creator)
        rows.append({"membership": membership, **match})
    return render(
        request,
        "campaigns/detail.html",
        {
            "campaign": campaign,
            "rows": rows,
            "invitations": campaign.invitations.select_related("creator"),
            "offers": campaign.offers.select_related("creator"),
            "feedback": campaign.feedback.select_related("creator", "user"),
            "spend": campaign.recorded_spend(),
            "remaining": campaign.remaining_budget(),
            "unrecorded": memberships.filter(recorded_cost__isnull=True).count(),
            "outreach_form": OutreachForm(initial={"creator_ids": ""}),
            "templates": MessageTemplate.objects.all(),
            "offer_form": OfferForm(),
            "cost_form": MembershipCostForm(),
            "invitation_form": InvitationOutcomeForm(),
            "edit_form": edit_form,
            "open_drawer": open_drawer,
        },
    )


@permission_required("campaigns")
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign.objects.select_related("niche", "subcategory", "owner"), pk=pk)
    return _render_campaign_detail(request, campaign)


@permission_required("campaigns")
@require_POST
def shortlist_creator(request, pk, creator_id):
    campaign = get_object_or_404(Campaign, pk=pk)
    creator = get_object_or_404(CreatorProfile, pk=creator_id)
    membership, created = CampaignCreator.objects.get_or_create(
        campaign=campaign,
        creator=creator,
        defaults={"added_by": request.user, "state": CampaignCreator.State.SHORTLISTED},
    )
    if created:
        messages.success(request, f"{creator.display_name} added to the shortlist.")
    else:
        messages.info(request, f"{creator.display_name} is already on this campaign.")
    creator.refresh_status()
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("campaigns:detail", pk=campaign.pk)


@permission_required("campaigns")
@require_POST
def assign_creator(request, pk, creator_id):
    campaign = get_object_or_404(Campaign, pk=pk)
    membership = get_object_or_404(CampaignCreator, campaign=campaign, creator_id=creator_id)
    membership.state = CampaignCreator.State.ASSIGNED
    cost = request.POST.get("recorded_cost")
    if cost:
        form = MembershipCostForm({"recorded_cost": cost})
        if form.is_valid():
            membership.recorded_cost = form.cleaned_data.get("recorded_cost")
    membership.save()
    membership.creator.refresh_status()
    messages.success(request, f"{membership.creator.display_name} assigned. Commercial acceptance still needs an offer decision.")
    return redirect("campaigns:detail", pk=pk)


@permission_required("campaigns")
@require_POST
def update_cost(request, pk, creator_id):
    campaign = get_object_or_404(Campaign, pk=pk)
    membership = get_object_or_404(CampaignCreator, campaign=campaign, creator_id=creator_id)
    form = MembershipCostForm(request.POST)
    if form.is_valid():
        membership.recorded_cost = form.cleaned_data.get("recorded_cost")
        membership.save(update_fields=["recorded_cost", "updated_at"])
        messages.success(request, "Recorded cost updated.")
    return redirect("campaigns:detail", pk=pk)


@permission_required("discover")
def discover(request, pk=None):
    campaign = get_object_or_404(Campaign, pk=pk) if pk else None
    form = DiscoverForm(request.GET or None)
    results = None
    if request.GET.get("query") or request.GET.get("run") or (campaign and request.GET.get("from_campaign")):
        if form.is_valid():
            criteria = None
            query = form.cleaned_data.get("query") or ""
            if campaign and not query:
                criteria = criteria_from_campaign(campaign)
            filters = {
                "location": form.cleaned_data.get("location") or "",
                "platform": form.cleaned_data.get("platform") or "",
            }
            if form.cleaned_data.get("niche"):
                filters["niche"] = form.cleaned_data["niche"].slug
            results = search_creators(
                query,
                criteria,
                page=request.GET.get("page") or 1,
                sort=form.cleaned_data.get("sort") or "relevance",
                filters=filters,
            )
    return render(
        request,
        "campaigns/discover.html",
        {
            "form": form,
            "results": results,
            "campaign": campaign,
            "feedback_form": FeedbackForm(),
            "open_drawer": "discoverDrawer" if form.errors else "",
        },
    )


@permission_required("discover")
@require_POST
def request_search_enrichment(request):
    creator_ids = [int(part) for part in request.POST.getlist("creator_id") if part.isdigit()]
    platform = request.POST.get("platform") or SocialAccount.Platform.INSTAGRAM
    queued = 0
    for creator in CreatorProfile.objects.filter(pk__in=creator_ids):
        _, created = enqueue_job(creator, platform, requested_by=request.user)
        if created:
            queued += 1
    if not creator_ids:
        messages.info(request, "No matching opted-in creators were selected. Jobs create backlog entries, not new profiles.")
    else:
        messages.success(request, f"Queued {queued} enrichment job(s).")
    return redirect(request.POST.get("next") or "campaigns:discover")


@permission_required("feedback")
@require_POST
def submit_feedback(request, pk, creator_id):
    campaign = get_object_or_404(Campaign, pk=pk)
    creator = get_object_or_404(CreatorProfile, pk=creator_id)
    form = FeedbackForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Feedback was not saved.")
        return redirect(request.POST.get("next") or "campaigns:detail", pk=pk)
    metrics = creator_metrics(creator)
    classification = metrics["classification"]
    snapshot = {}
    if classification:
        snapshot = {
            "id": classification.pk,
            "category": classification.category.slug if classification.category else None,
            "method": classification.method,
            "provenance": classification.provenance,
            "confidence": str(classification.confidence) if classification.confidence is not None else None,
        }
    RecommendationFeedback.objects.update_or_create(
        campaign=campaign,
        creator=creator,
        user=request.user,
        defaults={
            "decision": form.cleaned_data["decision"],
            "comment": form.cleaned_data.get("comment") or "",
            "classification_snapshot": snapshot,
            "recommendation_snapshot": {
                "followers": metrics["followers"],
                "engagement": metrics["engagement"],
                "niche": metrics["niche"],
                "location": metrics["location"],
            },
        },
    )
    messages.success(request, "Feedback saved. It will not retrain any model.")
    return redirect(request.POST.get("next") or "campaigns:feedback")


@permission_required("feedback")
def feedback_list(request):
    rows = RecommendationFeedback.objects.select_related("campaign", "creator", "user")
    campaign_id = request.GET.get("campaign")
    if campaign_id:
        rows = rows.filter(campaign_id=campaign_id)
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "campaigns/feedback.html",
        {"page": page, "campaigns": Campaign.objects.all(), "campaign_id": campaign_id},
    )


def _template_page(request, form, *, editing=None, open_form=False):
    return render(
        request,
        "campaigns/templates.html",
        {
            "form": form,
            "templates": MessageTemplate.objects.all(),
            "editing": editing,
            "open_form": open_form,
        },
    )


@permission_required("templates")
def template_list(request):
    form = TemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        template = form.save(commit=False)
        template.created_by = request.user
        template.save()
        messages.success(request, "Template saved.")
        return redirect("campaigns:templates")
    return _template_page(
        request,
        form,
        open_form=request.method == "POST" or request.GET.get("new") == "1",
    )


@permission_required("templates")
def template_edit(request, pk):
    template = get_object_or_404(MessageTemplate, pk=pk)
    form = TemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Template updated.")
        return redirect("campaigns:templates")
    return _template_page(request, form, editing=template, open_form=True)


@permission_required("outreach")
@require_POST
def prepare_outreach(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    form = OutreachForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a template and at least one creator.")
        return redirect("campaigns:detail", pk=pk)
    template = form.cleaned_data["template"]
    invitations = []
    for creator in CreatorProfile.objects.filter(pk__in=form.cleaned_data["creator_ids"]):
        subject, body = template.render(campaign=campaign, creator=creator)
        invitations.append(
            Invitation.objects.create(
                campaign=campaign,
                creator=creator,
                template=template,
                to_email=creator.user.email,
                subject=subject,
                body=body,
                follow_up_at=form.cleaned_data.get("follow_up_at"),
                created_by=request.user,
                status=Invitation.Status.DRAFT,
            )
        )
    request.session["pending_invitations"] = [item.pk for item in invitations]
    return redirect("campaigns:confirm_outreach", pk=pk)


@permission_required("outreach")
def confirm_outreach(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    ids = request.session.get("pending_invitations") or []
    invitations = list(Invitation.objects.filter(pk__in=ids, campaign=campaign, status=Invitation.Status.DRAFT))
    if request.method == "POST":
        if request.POST.get("confirm") != "1":
            messages.info(request, "Send cancelled.")
            return redirect("campaigns:detail", pk=pk)
        for invitation in invitations:
            send_invitation(invitation)
        request.session.pop("pending_invitations", None)
        messages.success(request, f"Processed {len(invitations)} invitation(s). Status reflects the configured email backend.")
        return redirect("campaigns:detail", pk=pk)
    return render(
        request,
        "campaigns/confirm_outreach.html",
        {"campaign": campaign, "invitations": invitations},
    )


@permission_required("outreach")
@require_POST
def invitation_outcome(request, pk):
    invitation = get_object_or_404(Invitation, pk=pk)
    form = InvitationOutcomeForm(request.POST)
    if form.is_valid():
        record_outcome(invitation, form.cleaned_data["status"], form.cleaned_data.get("note") or "")
        messages.success(request, "Invitation outcome recorded by a person. Delivery was not inferred.")
    return redirect("campaigns:detail", pk=invitation.campaign_id)


@permission_required("campaigns")
@require_POST
def create_offer(request, pk, creator_id):
    campaign = get_object_or_404(Campaign, pk=pk)
    creator = get_object_or_404(CreatorProfile, pk=creator_id)
    form = OfferForm(request.POST)
    if form.is_valid():
        offer = form.save(commit=False)
        offer.campaign = campaign
        offer.creator = creator
        offer.created_by = request.user
        offer.state = Offer.State.PROPOSED
        offer.save()
        notify_creator_offer(offer)
        messages.success(request, "Offer proposed. The creator can accept, reject, or counter.")
    return redirect("campaigns:detail", pk=pk)


@permission_required("campaigns")
@require_POST
def decide_offer(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    action = request.POST.get("action")
    if action == "accept":
        apply_offer_response(offer, accepted=True, actor=request.user, notify=False)
        messages.success(request, "Offer accepted by a team member.")
    elif action == "decline":
        apply_offer_response(offer, accepted=False, actor=request.user, notify=False)
        messages.success(request, "Offer declined.")
    return redirect("campaigns:detail", pk=offer.campaign_id)


@permission_required("campaigns")
def export_shortlist(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    return shortlist_csv(campaign)


@permission_required("outreach")
def outreach_history(request):
    rows = Invitation.objects.select_related("campaign", "creator")
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(request, "campaigns/outreach.html", {"page": page})

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import StudioProfile
from accounts.permissions import creator_required, team_required
from creators.classify import classify_creator
from creators.evaluation import evaluate_labels
from creators.forms import (
    ClassificationReviewForm,
    CreatorDocumentForm,
    CreatorJoinForm,
    CreatorProfileForm,
    EnrichmentRequestForm,
)
from creators.jobs import enqueue_job, process_pending_jobs, retry_job
from creators.metrics import creator_metrics
from creators.models import (
    Classification,
    ClassificationLabel,
    CreatorDocument,
    CreatorProfile,
    EnrichmentJob,
)
from creators.tools import bot_risk_indicators, brand_safety_hits, data_quality

User = get_user_model()


def join(request):
    if request.user.is_authenticated:
        if hasattr(request.user, "creator_profile"):
            return redirect("creators:me")
        return redirect("analytics:dashboard")
    form = CreatorJoinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        name = form.cleaned_data["full_name"].strip()
        first, _, last = name.partition(" ")
        username = _unique_username(email)
        user = User.objects.create_user(
            username=username,
            email=email,
            password=form.cleaned_data["password1"],
            first_name=first,
            last_name=last,
        )
        StudioProfile.objects.update_or_create(
            user=user,
            defaults={"role": StudioProfile.Role.CREATOR},
        )
        CreatorProfile.objects.create(
            user=user,
            display_name=name,
            location=form.cleaned_data.get("location") or "",
            discoverable=form.cleaned_data.get("discoverable") and form.cleaned_data.get("share_metrics"),
            share_metrics=form.cleaned_data.get("share_metrics"),
        )
        from allauth.account.models import EmailAddress

        address = EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)
        address.send_confirmation(request, signup=True)
        messages.info(request, "Check your email to verify the address before logging in.")
        return redirect("account_email_verification_sent")
    return render(request, "creators/join.html", {"form": form})


@creator_required
def me(request):
    profile = request.user.creator_profile
    form = CreatorProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        profile.refresh_status()
        messages.success(request, "Profile saved.")
        return redirect("creators:me")
    metrics = creator_metrics(profile)
    return render(
        request,
        "creators/me.html",
        {
            "profile": profile,
            "form": form,
            "metrics": metrics,
            "quality": data_quality(profile),
        },
    )


@creator_required
def documents(request):
    profile = request.user.creator_profile
    form = CreatorDocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        doc = form.save(commit=False)
        doc.creator = profile
        doc.save()
        messages.success(request, "Document uploaded.")
        return redirect("creators:documents")
    return render(
        request,
        "creators/documents.html",
        {"form": form, "documents": profile.documents.all()},
    )


@creator_required
def my_campaigns(request):
    profile = request.user.creator_profile
    memberships = profile.campaign_memberships.select_related("campaign", "campaign__niche")
    return render(request, "creators/my_campaigns.html", {"memberships": memberships})


@creator_required
def my_campaign(request, pk):
    from campaigns.forms import CounterOfferForm
    from campaigns.models import CampaignCreator, Offer

    profile = request.user.creator_profile
    membership = get_object_or_404(
        CampaignCreator.objects.select_related("campaign"),
        campaign_id=pk,
        creator=profile,
    )
    form = CounterOfferForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        Offer.objects.create(
            campaign=membership.campaign,
            creator=profile,
            amount=form.cleaned_data["amount"],
            currency=form.cleaned_data["currency"],
            note=form.cleaned_data.get("note") or "",
            state=Offer.State.COUNTERED,
            created_by=request.user,
        )
        messages.success(request, "Counteroffer recorded. A team member still has to accept commercial terms.")
        return redirect("creators:my_campaign", pk=pk)
    offers = Offer.objects.filter(campaign=membership.campaign, creator=profile)
    invitations = profile.invitations.filter(campaign=membership.campaign)
    return render(
        request,
        "creators/my_campaign.html",
        {
            "membership": membership,
            "campaign": membership.campaign,
            "offers": offers,
            "invitations": invitations,
            "form": form,
        },
    )


@team_required
def creator_detail(request, pk):
    creator = get_object_or_404(CreatorProfile.objects.select_related("user"), pk=pk)
    metrics = creator_metrics(creator)
    return render(
        request,
        "creators/detail.html",
        {
            "creator": creator,
            "metrics": metrics,
            "quality": data_quality(creator),
            "bot": bot_risk_indicators(creator),
            "safety": brand_safety_hits(creator),
            "classifications": creator.classifications.select_related("category", "subcategory")[:8],
            "jobs": creator.jobs.all()[:8],
            "enrich_form": EnrichmentRequestForm(),
            "review_form": ClassificationReviewForm(),
        },
    )


@team_required
@require_POST
def classify_now(request, pk):
    creator = get_object_or_404(CreatorProfile, pk=pk)
    method = request.POST.get("method") or "rules"
    if method not in ("rules", "anthropic"):
        method = "rules"
    classify_creator(creator, method=method, user=request.user)
    messages.success(request, "Classification saved. Metrics were not requested from Claude.")
    return redirect("creators:detail", pk=pk)


@team_required
@require_POST
def review_classification(request, pk):
    creator = get_object_or_404(CreatorProfile, pk=pk)
    form = ClassificationReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Classification review was not saved.")
        return redirect("creators:detail", pk=pk)
    Classification.objects.create(
        creator=creator,
        category=form.cleaned_data["category"],
        subcategory=form.cleaned_data.get("subcategory"),
        method=Classification.Method.MANUAL,
        confidence=1,
        evidence=[{"type": "manual_review", "value": form.cleaned_data.get("notes") or ""}],
        provenance=Classification.Provenance.VERIFIED,
        needs_review=False,
        explanation=form.cleaned_data.get("notes") or "Manually reviewed.",
        created_by=request.user,
    )
    if form.cleaned_data.get("verified_label"):
        ClassificationLabel.objects.update_or_create(
            creator=creator,
            defaults={
                "category": form.cleaned_data["category"],
                "subcategory": form.cleaned_data.get("subcategory"),
                "notes": form.cleaned_data.get("notes") or "",
                "labeled_by": request.user,
            },
        )
    messages.success(request, "Verified classification saved.")
    return redirect("creators:detail", pk=pk)


@team_required
def classification_queue(request):
    rows = Classification.objects.filter(needs_review=True).select_related("creator", "category")
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "creators/classification_queue.html",
        {"page": page, "evaluation": evaluate_labels()},
    )


@team_required
def jobs(request):
    rows = EnrichmentJob.objects.select_related("creator").all()
    status = request.GET.get("status")
    if status:
        rows = rows.filter(status=status)
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    from creators.jobs import backlog_counts

    return render(
        request,
        "creators/jobs.html",
        {"page": page, "counts": backlog_counts(), "status": status},
    )


@team_required
@require_POST
def request_enrichment(request, pk):
    creator = get_object_or_404(CreatorProfile, pk=pk)
    form = EnrichmentRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a platform.")
        return redirect("creators:detail", pk=pk)
    job, created = enqueue_job(creator, form.cleaned_data["platform"], requested_by=request.user)
    if created:
        messages.success(request, "Enrichment job queued. Run process_jobs to execute it.")
    else:
        messages.info(request, "An open job already exists for that platform.")
    return redirect("creators:detail", pk=pk)


@team_required
@require_POST
def retry_enrichment(request, pk):
    job = get_object_or_404(EnrichmentJob, pk=pk)
    retry_job(job)
    messages.success(request, "Job returned to the pending queue.")
    return redirect("creators:jobs")


@team_required
@require_POST
def run_jobs(request):
    processed = process_pending_jobs()
    messages.success(request, f"Processed {len(processed)} job(s) in this batch.")
    return redirect("creators:jobs")


@login_required
def creator_redirect(request):
    if hasattr(request.user, "creator_profile"):
        return redirect("creators:me")
    return redirect("analytics:dashboard")


def _unique_username(email):
    base = email.split("@")[0][:140] or "creator"
    username = base
    index = 1
    while User.objects.filter(username=username).exists():
        username = f"{base}{index}"
        index += 1
    return username

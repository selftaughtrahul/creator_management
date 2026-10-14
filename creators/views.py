from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import StudioProfile
from accounts.permissions import creator_required, permission_required
from accounts.utils import unique_username
from creators.classify import classify_creator
from creators.evaluation import evaluate_labels
from creators.forms import (
    ClassificationReviewForm,
    CreatorDocumentForm,
    CreatorJoinForm,
    CreatorProfileForm,
    EnrichmentRequestForm,
)
from campaigns.search import search_creators
from creators.jobs import enqueue_job, process_pending_jobs, retry_job
from creators.metrics import account_metrics, creator_metrics, growth_summary
from creators.parser import chat_turn, criteria_active, empty_criteria
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
        from accounts.permissions import home_url

        return redirect(home_url(request.user))
    form = CreatorJoinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        name = form.cleaned_data["full_name"].strip()
        first, _, last = name.partition(" ")
        user = User.objects.create_user(
            username=unique_username(email),
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
            "open_drawer": "profileDrawer" if request.method == "POST" else "",
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
        {
            "form": form,
            "documents": profile.documents.all(),
            "open_drawer": "documentDrawer" if request.method == "POST" or request.GET.get("new") == "1" else "",
        },
    )


@creator_required
def my_campaigns(request):
    profile = request.user.creator_profile
    memberships = profile.campaign_memberships.select_related("campaign", "campaign__niche")
    invitations = profile.invitations.select_related("campaign")
    return render(
        request,
        "creators/my_campaigns.html",
        {"memberships": memberships, "invitations": invitations},
    )


@creator_required
def my_campaign(request, pk):
    from campaigns.forms import CounterOfferForm
    from campaigns.models import Campaign, CampaignCreator, Offer

    profile = request.user.creator_profile
    campaign = get_object_or_404(Campaign, pk=pk)
    membership = CampaignCreator.objects.filter(campaign=campaign, creator=profile).first()
    invited = profile.invitations.filter(campaign=campaign).exists()
    if not membership and not invited:
        from django.http import Http404

        raise Http404
    form = CounterOfferForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "counter" and form.is_valid():
        from accounts.notify import notify_staff_offer_response

        offer = Offer.objects.create(
            campaign=campaign,
            creator=profile,
            amount=form.cleaned_data["amount"],
            currency=form.cleaned_data["currency"],
            note=form.cleaned_data.get("note") or "",
            state=Offer.State.COUNTERED,
            created_by=request.user,
        )
        notify_staff_offer_response(offer)
        messages.success(request, "Counteroffer sent. Admins and employees will be notified.")
        return redirect("creators:my_campaign", pk=pk)
    offers = Offer.objects.filter(campaign=campaign, creator=profile)
    invitations = profile.invitations.filter(campaign=campaign)
    return render(
        request,
        "creators/my_campaign.html",
        {
            "membership": membership,
            "campaign": campaign,
            "offers": offers,
            "invitations": invitations,
            "form": form,
            "open_drawer": "counterDrawer" if request.method == "POST" else "",
        },
    )


@creator_required
def invitations(request):
    profile = request.user.creator_profile
    rows = profile.invitations.select_related("campaign")
    return render(request, "creators/invitations.html", {"invitations": rows})


@creator_required
@require_POST
def respond_invitation(request, pk):
    from campaigns.models import Invitation
    from campaigns.status import OPEN_INVITATIONS, apply_invitation_response

    profile = request.user.creator_profile
    invitation = get_object_or_404(Invitation, pk=pk, creator=profile)
    if invitation.status not in OPEN_INVITATIONS:
        messages.error(request, "This invitation is no longer waiting for a response.")
        return redirect("creators:invitations")
    accepted = request.POST.get("decision") == "accept"
    apply_invitation_response(
        invitation,
        accepted=accepted,
        actor=request.user,
        note=request.POST.get("note") or "",
    )
    messages.success(request, "Accepted." if accepted else "Declined.")
    return redirect("creators:invitations")


@creator_required
@require_POST
def respond_offer(request, pk):
    from campaigns.models import Offer
    from campaigns.status import OPEN_OFFERS, apply_offer_response

    profile = request.user.creator_profile
    offer = get_object_or_404(Offer, pk=pk, creator=profile)
    if offer.state not in OPEN_OFFERS:
        messages.error(request, "This offer is no longer waiting for a response.")
        return redirect("creators:my_campaign", pk=offer.campaign_id)
    accepted = request.POST.get("decision") == "accept"
    apply_offer_response(
        offer,
        accepted=accepted,
        actor=request.user,
        note=request.POST.get("note") or "",
    )
    messages.success(request, "Offer accepted." if accepted else "Offer declined.")
    return redirect("creators:my_campaign", pk=offer.campaign_id)


DISCOVER_SESSION = "creator_discover_chat"


@permission_required("creators")
def creator_directory(request):
    query = (request.GET.get("q") or "").strip()
    chat = request.session.get(DISCOVER_SESSION) or {}
    criteria = chat.get("criteria") or empty_criteria()
    results = None
    if criteria_active(criteria):
        results = search_creators(
            criteria=criteria,
            page=request.GET.get("page") or 1,
            per_page=12,
            filters={"name": query} if query else None,
        )
        results["parsed"] = {"parser": chat.get("parser") or "rules", "raw_query": ""}
        page = results["page"]
        cards = list(page.object_list)
    else:
        rows = CreatorProfile.objects.select_related("user").prefetch_related(
            "niches",
            "user__social_accounts__snapshots__breakdowns",
            "user__social_accounts__contents",
            "classifications__category",
            "classifications__subcategory",
        )
        if query:
            rows = rows.filter(display_name__icontains=query)
        page = Paginator(rows, 12).get_page(request.GET.get("page"))
        cards = [{"creator": creator, "metrics": creator_metrics(creator)} for creator in page]
    for card in cards:
        card["accounts"] = account_metrics(card["creator"])
        card["growth"] = growth_summary(card["creator"])
    return render(
        request,
        "creators/directory.html",
        {
            "page": page,
            "cards": cards,
            "q": query,
            "chat_messages": chat.get("messages") or [],
            "results": results,
            "open_discover": request.GET.get("chat") == "1",
        },
    )


@permission_required("creators")
@require_POST
def discover_chat(request):
    if request.POST.get("clear"):
        request.session.pop(DISCOVER_SESSION, None)
        return redirect("creators:directory")
    message = (request.POST.get("message") or "").strip()
    state = request.session.get(DISCOVER_SESSION) or {
        "messages": [],
        "criteria": empty_criteria(),
        "parser": "rules",
    }
    if message:
        turn = chat_turn(state.get("messages") or [], state.get("criteria") or empty_criteria(), message)
        history = list(state.get("messages") or [])
        history.append({"role": "user", "text": message})
        history.append({"role": "assistant", "text": turn["reply"]})
        request.session[DISCOVER_SESSION] = {
            "messages": history[-16:],
            "criteria": turn["criteria"],
            "parser": turn["parser"],
        }
        request.session.modified = True
    return redirect(f"{reverse('creators:directory')}?chat=1")


@permission_required("creators")
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


@permission_required("creators")
@require_POST
def classify_now(request, pk):
    creator = get_object_or_404(CreatorProfile, pk=pk)
    method = request.POST.get("method") or "rules"
    if method not in ("rules", "anthropic"):
        method = "rules"
    classify_creator(creator, method=method, user=request.user)
    messages.success(request, "Classification saved. Metrics were not requested from Claude.")
    return redirect("creators:detail", pk=pk)


@permission_required("creators")
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


@permission_required("classifications")
def classification_queue(request):
    rows = Classification.objects.filter(needs_review=True).select_related("creator", "category")
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "creators/classification_queue.html",
        {"page": page, "evaluation": evaluate_labels()},
    )


@permission_required("jobs")
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


@permission_required("creators")
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


@permission_required("jobs")
@require_POST
def retry_enrichment(request, pk):
    job = get_object_or_404(EnrichmentJob, pk=pk)
    retry_job(job)
    messages.success(request, "Job returned to the pending queue.")
    return redirect("creators:jobs")


@permission_required("jobs")
@require_POST
def run_jobs(request):
    processed = process_pending_jobs()
    messages.success(request, f"Processed {len(processed)} job(s) in this batch.")
    return redirect("creators:jobs")


@login_required
def creator_redirect(request):
    from accounts.permissions import home_url

    if hasattr(request.user, "creator_profile"):
        return redirect("creators:me")
    return redirect(home_url(request.user))

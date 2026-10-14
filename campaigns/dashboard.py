from datetime import datetime, time

from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts.models import StudioProfile
from campaigns.models import Campaign, CampaignCreator, Invitation, Offer, RecommendationFeedback
from creators.evaluation import evaluate_labels
from creators.jobs import backlog_counts
from creators.models import Classification, CreatorProfile, EnrichmentJob
from networks.models import SocialAccount


def studio_dashboard(request):
    start, end = _range(request)
    campaign_id = request.GET.get("campaign")
    campaigns = Campaign.objects.all()
    scoped = campaigns
    if campaign_id:
        scoped = campaigns.filter(pk=campaign_id)
    if start:
        scoped = scoped.filter(created_at__gte=start)
    if end:
        scoped = scoped.filter(created_at__lte=end)

    status_counts = {key: 0 for key, _ in Campaign.Status.choices}
    for row in scoped.values("status").annotate(total=Count("id")):
        status_counts[row["status"]] = row["total"]

    memberships = CampaignCreator.objects.filter(campaign__in=scoped)
    shortlisted = memberships.filter(state=CampaignCreator.State.SHORTLISTED).values("creator").distinct().count()
    assigned = memberships.filter(state=CampaignCreator.State.ASSIGNED).values("creator").distinct().count()
    discovered = CreatorProfile.objects.filter(discoverable=True).count()

    invitations = Invitation.objects.filter(campaign__in=scoped)
    invite_counts = {key: 0 for key, _ in Invitation.Status.choices}
    for row in invitations.values("status").annotate(total=Count("id")):
        invite_counts[row["status"]] = row["total"]
    sent_like = invite_counts.get("sent", 0) + invite_counts.get("stored_locally", 0)
    responses = (
        invite_counts.get("responded", 0)
        + invite_counts.get("accepted", 0)
        + invite_counts.get("rejected", 0)
    )

    onboarding = {key: 0 for key, _ in CreatorProfile.Status.choices}
    for row in CreatorProfile.objects.values("status").annotate(total=Count("id")):
        onboarding[row["status"]] = row["total"]

    budget_set = scoped.exclude(budget__isnull=True)
    budget_sum = budget_set.aggregate(total=Sum("budget")).get("total")
    cost_sum = memberships.aggregate(total=Sum("recorded_cost")).get("total")
    unrecorded = memberships.filter(recorded_cost__isnull=True).count()

    feedback = RecommendationFeedback.objects.filter(campaign__in=scoped)
    feedback_total = feedback.count()
    yes_count = feedback.filter(decision=RecommendationFeedback.Decision.YES).count()
    acceptance = round(yes_count / feedback_total * 100, 1) if feedback_total else None

    labels_eval = evaluate_labels()
    jobs = backlog_counts()
    due_followups = invitations.filter(
        follow_up_at__isnull=False,
        follow_up_at__lte=timezone.now(),
        status__in=[
            Invitation.Status.SENT,
            Invitation.Status.STORED_LOCALLY,
            Invitation.Status.CONFIRMED,
        ],
    ).count()

    team_activity = (
        RecommendationFeedback.objects.filter(campaign__in=scoped)
        .values("user__email")
        .annotate(total=Count("id"))
        .order_by("-total")[:8]
    )
    offers = Offer.objects.filter(campaign__in=scoped)
    offer_counts = {key: 0 for key, _ in Offer.State.choices}
    for row in offers.values("state").annotate(total=Count("id")):
        offer_counts[row["state"]] = row["total"]

    recent_campaigns = list(scoped.select_related("owner", "niche")[:12])
    review_queue = Classification.objects.filter(needs_review=True).select_related("creator", "category")[:8]
    recent_jobs = EnrichmentJob.objects.select_related("creator")[:8]

    charts = {
        "campaigns": _doughnut(
            [label for _, label in Campaign.Status.choices],
            [status_counts[key] for key, _ in Campaign.Status.choices],
            ["#c2410c", "#0f766e", "#a16207", "#1d4ed8"],
        ),
        "invites": _doughnut(
            [label for _, label in Invitation.Status.choices],
            [invite_counts[key] for key, _ in Invitation.Status.choices],
            ["#78716c", "#a8a29e", "#0f766e", "#1d4ed8", "#9f1239", "#c2410c", "#166534", "#991b1b"],
        ),
    }

    return {
        "start": start,
        "end": end,
        "campaign_filter": campaign_id,
        "all_campaigns": campaigns,
        "kpis": {
            "campaigns_total": scoped.count(),
            "campaigns_active": status_counts.get(Campaign.Status.ACTIVE, 0),
            "campaigns_completed": status_counts.get(Campaign.Status.COMPLETED, 0),
            "creators_discovered": discovered,
            "creators_shortlisted": shortlisted,
            "creators_assigned": assigned,
            "invitations_sent": sent_like,
            "invitations_responses": responses,
            "budget_sum": budget_sum,
            "cost_sum": cost_sum,
            "unrecorded_costs": unrecorded,
            "acceptance_rate": acceptance,
            "feedback_total": feedback_total,
            "due_followups": due_followups,
            "admins": StudioProfile.objects.filter(
                role__in=[StudioProfile.Role.ADMIN, StudioProfile.Role.TEAM]
            ).count(),
            "employees": StudioProfile.objects.filter(role=StudioProfile.Role.EMPLOYEE).count(),
            "creators_total": StudioProfile.objects.filter(role=StudioProfile.Role.CREATOR).count(),
            "social_accounts": SocialAccount.objects.count(),
        },
        "definitions": {
            "campaigns_total": "Count of Campaign rows in the selected date/campaign filter.",
            "campaigns_active": "Campaigns with status=active in that set.",
            "creators_discovered": "CreatorProfile rows with discoverable=True (not filtered by campaign).",
            "creators_shortlisted": "Distinct creators on CampaignCreator with state=shortlisted.",
            "invitations_sent": "Invitations with status sent or stored_locally. Delivered is not recorded.",
            "budget_sum": "Sum of Campaign.budget where budget is set. Campaigns without a budget are omitted.",
            "cost_sum": "Sum of CampaignCreator.recorded_cost. Null costs are unrecorded, not treated as zero.",
            "acceptance_rate": "RecommendationFeedback decision=yes divided by all feedback rows. Hidden when none exist.",
            "admins": "StudioProfile rows with role admin (legacy team counts as admin).",
            "employees": "StudioProfile rows with role employee.",
            "creators_total": "StudioProfile rows with role creator.",
            "social_accounts": "Connected SocialAccount rows across every user.",
        },
        "status_counts": status_counts,
        "invite_counts": invite_counts,
        "onboarding": onboarding,
        "offer_counts": offer_counts,
        "jobs": jobs,
        "evaluation": labels_eval,
        "review_queue": review_queue,
        "recent_jobs": recent_jobs,
        "recent_campaigns": recent_campaigns,
        "team_activity": team_activity,
        "charts": charts,
        "missing": _missing(budget_sum, cost_sum, acceptance, labels_eval),
    }


def _range(request):
    start = parse_date(request.GET.get("from") or "")
    end = parse_date(request.GET.get("to") or "")
    start_dt = timezone.make_aware(datetime.combine(start, time.min)) if start else None
    end_dt = timezone.make_aware(datetime.combine(end, time.max)) if end else None
    return start_dt, end_dt


def _doughnut(labels, data, colors):
    if not any(data):
        return None
    return {
        "type": "doughnut",
        "data": {
            "labels": labels,
            "datasets": [{"data": data, "backgroundColor": colors[: len(labels)], "borderWidth": 0}],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"position": "bottom"}},
        },
    }


def _missing(budget_sum, cost_sum, acceptance, evaluation):
    notes = []
    if budget_sum is None:
        notes.append("No campaign in this filter has a budget set.")
    if cost_sum is None:
        notes.append("No recorded creator costs in this filter.")
    if acceptance is None:
        notes.append("No recommendation feedback rows, so acceptance rate is not shown.")
    if not evaluation["ready"]:
        notes.append(evaluation["message"])
    return notes

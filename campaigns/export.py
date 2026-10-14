import csv

from django.http import HttpResponse

from creators.metrics import creator_metrics


def shortlist_csv(campaign):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="campaign-{campaign.pk}-shortlist.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "creator_id",
            "name",
            "location",
            "followers",
            "followers_source",
            "engagement",
            "engagement_source",
            "niche",
            "niche_source",
            "platforms",
            "state",
            "recorded_cost",
        ]
    )
    for membership in campaign.memberships.select_related("creator"):
        metrics = creator_metrics(membership.creator)
        writer.writerow(
            [
                membership.creator_id,
                membership.creator.display_name,
                metrics["location"]["value"] or "",
                metrics["followers"]["value"] if metrics["followers"]["value"] is not None else "",
                metrics["followers"]["source"],
                metrics["engagement"]["value"] if metrics["engagement"]["value"] is not None else "",
                metrics["engagement"]["source"],
                metrics["niche"]["value"] or "",
                metrics["niche"]["source"],
                ",".join(metrics["platforms"]),
                membership.state,
                membership.recorded_cost if membership.recorded_cost is not None else "",
            ]
        )
    return response

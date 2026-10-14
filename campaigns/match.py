from creators.parser import criteria_from_campaign
from campaigns.search import score_creator
from creators.metrics import creator_metrics


def match_campaign(campaign, creator):
    metrics = creator_metrics(creator)
    result = score_creator(metrics, criteria_from_campaign(campaign))
    return {"metrics": metrics, **result}

from collections import defaultdict

from creators.models import Classification, ClassificationLabel


def evaluate_labels(labels=None):
    """Precision, recall, and F1 on manually labeled examples only.

    A production baseline is not claimed until labels exist. This function
    never invents an accuracy score.
    """
    if labels is None:
        labels = list(
            ClassificationLabel.objects.select_related("creator", "category", "subcategory")
        )
    else:
        labels = list(labels)
    if not labels:
        return {
            "ready": False,
            "labeled": 0,
            "predicted": 0,
            "correct": 0,
            "precision": None,
            "recall": None,
            "f1": None,
            "message": "No manually verified labels exist. A classification baseline is not established.",
            "per_category": [],
        }
    latest = _latest_by_creator([label.creator_id for label in labels])
    predicted = 0
    correct = 0
    per = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "name": ""})
    for label in labels:
        classification = latest.get(label.creator_id)
        predicted_id = classification.category_id if classification else None
        if predicted_id:
            predicted += 1
        cat = per[label.category_id]
        cat["name"] = label.category.name
        if predicted_id == label.category_id:
            correct += 1
            cat["tp"] += 1
        else:
            cat["fn"] += 1
            if predicted_id:
                other = per[predicted_id]
                other["name"] = classification.category.name if classification and classification.category else "Unknown"
                other["fp"] += 1
    precision = _ratio(correct, predicted)
    recall = _ratio(correct, len(labels))
    return {
        "ready": True,
        "labeled": len(labels),
        "predicted": predicted,
        "correct": correct,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "message": (
            f"Scores are computed on {len(labels)} manually labeled example"
            f"{'s' if len(labels) != 1 else ''} only."
        ),
        "per_category": [
            {
                "category_id": category_id,
                "name": stats["name"],
                "precision": _ratio(stats["tp"], stats["tp"] + stats["fp"]),
                "recall": _ratio(stats["tp"], stats["tp"] + stats["fn"]),
                "f1": _f1(
                    _ratio(stats["tp"], stats["tp"] + stats["fp"]),
                    _ratio(stats["tp"], stats["tp"] + stats["fn"]),
                ),
            }
            for category_id, stats in per.items()
        ],
    }


def _latest_by_creator(creator_ids):
    rows = (
        Classification.objects.filter(creator_id__in=creator_ids)
        .select_related("category")
        .order_by("creator_id", "-created_at")
    )
    latest = {}
    for row in rows:
        latest.setdefault(row.creator_id, row)
    return latest


def _ratio(numerator, denominator):
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _f1(precision, recall):
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)

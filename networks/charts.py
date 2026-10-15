"""Chart.js specs for audience gender and demographic breakdowns."""

from networks.countries import country_label

GENDER_COLORS = {
    "Female": "#c2410c",
    "Male": "#1d4ed8",
    "Unknown": "#a8a29e",
    "User specified": "#78716c",
}
GENDER_LABELS = {
    "f": "Female",
    "female": "Female",
    "m": "Male",
    "male": "Male",
    "u": "Unknown",
    "unknown": "Unknown",
    "user_specified": "User specified",
}
DIMENSION_TITLES = {
    "country": "Country",
    "gender": "Gender",
    "age": "Age",
    "city": "City",
    "gender_age": "Age and gender",
    "age_gender": "Age and gender",
}


def number(value):
    if value is None or value == "":
        return 0
    return float(value)


def doughnut(labels, values):
    if not labels:
        return None
    return {
        "type": "doughnut",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "data": values,
                    "backgroundColor": [GENDER_COLORS.get(label, "#78716c") for label in labels],
                    "borderWidth": 0,
                }
            ],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"position": "bottom"}},
        },
    }


def bar(labels, values, color, label="Audience"):
    if not labels:
        return None
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": label,
                    "data": values,
                    "backgroundColor": color,
                    "borderRadius": 6,
                }
            ],
        },
        "options": {
            "indexAxis": "y",
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {"x": {"beginAtZero": True}},
        },
    }


def chart_block(prefix, key, title, spec):
    if not spec:
        return None
    canvas = f"{prefix}-{key}"
    return {"title": title, "canvas": canvas, "spec_id": f"{canvas}-spec", "spec": spec}


def segment_label(dimension, segment):
    text = str(segment or "")
    if dimension == "gender":
        return GENDER_LABELS.get(text.strip().lower(), text)
    if dimension == "country":
        return country_label(text)
    return text


def charts_from_breakdowns(rows, prefix, color="#0f766e"):
    grouped = {}
    for row in rows:
        grouped.setdefault(row.dimension, []).append(row)
    charts = []
    for dimension, items in grouped.items():
        ranked = sorted(items, key=lambda item: number(item.value), reverse=True)[:12]
        labels = [segment_label(dimension, item.segment) for item in ranked]
        values = [number(item.value) for item in ranked]
        if dimension == "gender":
            spec = doughnut(labels, values)
        else:
            spec = bar(labels, values, color, DIMENSION_TITLES.get(dimension, "Audience"))
        block = chart_block(prefix, dimension, DIMENSION_TITLES.get(dimension, dimension.replace("_", " ").title()), spec)
        if block:
            charts.append(block)
    return charts

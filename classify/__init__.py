"""Import the IAB taxonomy and classify a creator.

    from classify import import_taxonomy, classify

    import_taxonomy()
    labels = classify(creator)
    labels = classify(creator, mode="content", method="rules")
"""

import importlib

__all__ = [
    "IABTaxonomyError",
    "classify",
    "classify_niche",
    "classify_with_rules",
    "collect_chunks",
    "import_taxonomy",
    "parse_taxonomy_tsv",
]

_EXPORTS = {
    "IABTaxonomyError": ("classify.taxonomy", "IABTaxonomyError"),
    "classify": ("classify.pipeline", "classify"),
    "classify_niche": ("classify.niche", "classify_niche"),
    "classify_with_rules": ("classify.niche", "classify_with_rules"),
    "collect_chunks": ("classify.pipeline", "collect_chunks"),
    "import_taxonomy": ("classify.taxonomy", "import_taxonomy"),
    "parse_taxonomy_tsv": ("classify.taxonomy", "parse_taxonomy_tsv"),
}


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr = target
    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value

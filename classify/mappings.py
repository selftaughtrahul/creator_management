"""Links official Content Taxonomy 3.1 IDs to Awisee discovery labels.

IDs and expected names were taken from the vendored TSV. A mapping is skipped
when the imported row is missing or its name no longer matches.
"""

STARTER_MAPPINGS = [
    {
        "iab_category_id": "680",
        "expected_name": "Video Gaming",
        "custom_niche": "Gaming",
        "custom_sub_niche": "Video games",
        "custom_micro_niche": "",
        "keywords": "gaming, gamer, gameplay, esports, video game",
    },
    {
        "iab_category_id": "553",
        "expected_name": "Beauty",
        "custom_niche": "Beauty",
        "custom_sub_niche": "Makeup",
        "custom_micro_niche": "",
        "keywords": "makeup, skincare, cosmetics, beauty",
    },
    {
        "iab_category_id": "552",
        "expected_name": "Style & Fashion",
        "custom_niche": "Fashion",
        "custom_sub_niche": "",
        "custom_micro_niche": "",
        "keywords": "fashion, outfit, streetwear, lookbook",
    },
    {
        "iab_category_id": "210",
        "expected_name": "Food & Drink",
        "custom_niche": "Food",
        "custom_sub_niche": "",
        "custom_micro_niche": "",
        "keywords": "food, cooking, recipe, street food",
    },
    {
        "iab_category_id": "225",
        "expected_name": "Fitness and Exercise",
        "custom_niche": "Fitness",
        "custom_sub_niche": "Training",
        "custom_micro_niche": "",
        "keywords": "fitness, workout, gym, yoga",
    },
    {
        "iab_category_id": "596",
        "expected_name": "Technology & Computing",
        "custom_niche": "Tech",
        "custom_sub_niche": "",
        "custom_micro_niche": "",
        "keywords": "gadget, smartphone, software",
    },
    {
        "iab_category_id": "597",
        "expected_name": "Artificial Intelligence",
        "custom_niche": "Tech",
        "custom_sub_niche": "Artificial Intelligence",
        "custom_micro_niche": "AI Agent Development",
        "keywords": "artificial intelligence, llm, llms, langgraph, ai agent, tool calling",
    },
    {
        "iab_category_id": "653",
        "expected_name": "Travel",
        "custom_niche": "Lifestyle",
        "custom_sub_niche": "Travel",
        "custom_micro_niche": "",
        "keywords": "travel, hotel, destination",
    },
    {
        "iab_category_id": "132",
        "expected_name": "Education",
        "custom_niche": "Education",
        "custom_sub_niche": "",
        "custom_micro_niche": "",
        "keywords": "coursework, college education, online education",
    },
]

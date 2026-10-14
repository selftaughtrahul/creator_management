STARTER_CATEGORIES = [
    {
        "name": "Gaming",
        "slug": "gaming",
        "keywords": "gaming, gamer, gameplay, esports, streamer, playthrough, let's play",
        "description": "Video game creators.",
        "subcategories": [
            {
                "name": "Mobile gaming",
                "slug": "mobile-gaming",
                "keywords": "mobile gaming, android game, ios game, pubg, free fire, bgmi, mobile legends, clash",
            },
            {
                "name": "PC gaming",
                "slug": "pc-gaming",
                "keywords": "pc gaming, steam, fps, rpg, minecraft, valorant, league of legends",
            },
            {
                "name": "Console",
                "slug": "console",
                "keywords": "playstation, xbox, nintendo, console, switch",
            },
        ],
    },
    {
        "name": "Beauty",
        "slug": "beauty",
        "keywords": "beauty, makeup, skincare, cosmetics, glam",
        "description": "Beauty and personal care.",
        "subcategories": [
            {
                "name": "Makeup",
                "slug": "makeup",
                "keywords": "makeup, lipstick, foundation, tutorial",
            },
            {
                "name": "Skincare",
                "slug": "skincare",
                "keywords": "skincare, serum, routine, dermatology",
            },
        ],
    },
    {
        "name": "Fashion",
        "slug": "fashion",
        "keywords": "fashion, style, outfit, clothing, lookbook",
        "description": "Clothing and style.",
        "subcategories": [
            {
                "name": "Streetwear",
                "slug": "streetwear",
                "keywords": "streetwear, sneakers, hype, drips",
            }
        ],
    },
    {
        "name": "Food",
        "slug": "food",
        "keywords": "food, cooking, recipe, restaurant, chef",
        "description": "Food and cooking.",
        "subcategories": [
            {
                "name": "Street food",
                "slug": "street-food",
                "keywords": "street food, stall, vendor, chaat",
            }
        ],
    },
    {
        "name": "Fitness",
        "slug": "fitness",
        "keywords": "fitness, gym, workout, training, health",
        "description": "Fitness and training.",
        "subcategories": [
            {
                "name": "Yoga",
                "slug": "yoga",
                "keywords": "yoga, pilates, stretch",
            }
        ],
    },
    {
        "name": "Tech",
        "slug": "tech",
        "keywords": "tech, gadget, review, smartphone, software",
        "description": "Consumer technology.",
        "subcategories": [
            {
                "name": "Mobile tech",
                "slug": "mobile-tech",
                "keywords": "smartphone, android, iphone, mobile review",
            }
        ],
    },
    {
        "name": "Lifestyle",
        "slug": "lifestyle",
        "keywords": "lifestyle, vlog, daily, travel, home",
        "description": "Lifestyle and daily life.",
        "subcategories": [
            {
                "name": "Travel",
                "slug": "travel",
                "keywords": "travel, trip, hotel, destination",
            }
        ],
    },
    {
        "name": "Education",
        "slug": "education",
        "keywords": "education, tutorial, explain, course, learn",
        "description": "Teaching and explainers.",
        "subcategories": [
            {
                "name": "Career",
                "slug": "career",
                "keywords": "career, job, interview, resume",
            }
        ],
    },
]


def ensure_taxonomy():
    from creators.models import Category, Subcategory

    for item in STARTER_CATEGORIES:
        category, _ = Category.objects.update_or_create(
            slug=item["slug"],
            defaults={
                "name": item["name"],
                "keywords": item["keywords"],
                "description": item.get("description", ""),
            },
        )
        for sub in item.get("subcategories", []):
            Subcategory.objects.update_or_create(
                category=category,
                slug=sub["slug"],
                defaults={"name": sub["name"], "keywords": sub["keywords"]},
            )

# How to use the IAB Content Taxonomy

Use IAB Content Taxonomy 3.1 as the shared category foundation, and keep Awisee niches, sub-niches, and micro-niches as a separate layer for influencer discovery.

IAB supplies category names and stable identifiers. It does not classify creators. Classification still has to read Instagram posts and YouTube videos, then save a result that points at a real IAB row plus any custom labels that row cannot express.

## Decision

| Layer | Owner | Used for |
| --- | --- | --- |
| IAB category | Official taxonomy, imported from the TSV | Stable IDs, reporting, and a common language with ad/contextual systems |
| Niche, sub-niche, micro-niche | Awisee (`Category`, `Subcategory`, and finer custom labels) | Discovery, shortlists, and campaign matching |

A creator can have several of both. One YouTube channel about AI agents and product reviews is both Technology and a product niche, not a single forced label.

## 1. Download the taxonomy

Source repository: [InteractiveAdvertisingBureau/Taxonomies](https://github.com/InteractiveAdvertisingBureau/Taxonomies)

File to import:

`Content Taxonomies/Content Taxonomy 3.1.tsv`

Raw URL:

https://raw.githubusercontent.com/InteractiveAdvertisingBureau/Taxonomies/main/Content%20Taxonomies/Content%20Taxonomy%203.1.tsv

Vendor that file into the project (for example `data/taxonomies/Content Taxonomy 3.1.tsv`) and import it with a management command. Do not copy category names into `creators/taxonomy.py` by hand.

Before importing, confirm three things in the file itself:

- The second header cell group says `Content Taxonomy v3.1`. Content Taxonomy 3.x is not interchangeable with 1.x or 2.x. Deleted IDs from older versions must not be reused.
- Columns on the second header row are `Unique ID`, `Parent`, `Name`, `Tier 1`, `Tier 2`, `Tier 3`, `Tier 4`.
- `Unique ID` is a string. Most values are numeric (`597`), but some are alphanumeric (`MQ2XML`). Store the ID exactly as published.

The file fetched for this guide had 704 data rows. Count rows at import time and record that count with the version. Re-download when IAB publishes a newer minor update, and keep the previous file so old classifications still resolve.

Attribute the taxonomy when you vendor it. IAB Tech Lab publishes Content Taxonomy 3.1 under [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). Confirm the license text in the repository at the time you copy the file.

### What one row means

```
Unique ID  Parent  Name                         Tier 1                    Tier 2
596                Technology & Computing       Technology & Computing
597        596     Artificial Intelligence      Technology & Computing    Artificial Intelligence
```

`597` is the official category for Artificial Intelligence. Its parent is `596`. There is no official row for “AI Agent Development,” “LangGraph,” or similar creator micro-niches. Those stay in the custom layer and point back at `597`.

## 2. What to store

Keep the official table and the Awisee mapping separate.

**Official category** — one row per TSV line:

- `unique_id` (string, unique)
- `parent_id` (string, blank for a tier-1 row)
- `name`
- `tier_1`, `tier_2`, `tier_3`, `tier_4`
- `taxonomy_version` = `3.1`

Never mint an ID that is not in the file. If a label is not in the import, it is a custom niche.

**Mapping** — how an official category relates to discovery labels. One IAB category can map to many custom paths, and one creator can have many mappings.

```json
{
  "iab_taxonomy_version": "3.1",
  "iab_category_id": "597",
  "iab_category_name": "Artificial Intelligence",
  "iab_parent_id": "596",
  "custom_niche": "Tech",
  "custom_sub_niche": "Artificial Intelligence",
  "custom_micro_niche": "AI Agent Development"
}
```

`597` and `Artificial Intelligence` come from the TSV. `Tech` already exists as a `Category` slug `tech` in `creators/taxonomy.py`. `AI Agent Development` is an Awisee micro-niche, not an IAB name.

Today `Classification` stores one `category` and one `subcategory` per run. `CreatorProfile.niches` is already a many-to-many on `Subcategory`, which is the right place for several discovery labels. When you add IAB, attach one or more official IDs to that same classification result instead of replacing `Category` / `Subcategory`.

## 3. Classification pipeline

Run this only after the TSV import and the mapping table exist.

```
Instagram posts + YouTube videos already in PostgreSQL
        |
        v
Read titles, descriptions, captions, and stored transcripts
        |
        v
Embed that text
        |
        v
Qdrant  (retrieve the creator's most relevant posts/videos)
        |
        v
LLM classification against the imported IAB names and IDs
        |
        v
Keep the official ID (example: 597)
        |
        v
Add custom niche / sub-niche / micro-niche when the brief needs them
        |
        v
Save the result on the creator in PostgreSQL
```

Qdrant retrieves content. It does not assign a category. The model must choose an ID that exists in the imported table. Reject any ID that was not imported.

Use content in this order when it exists:

1. Profile only: `CreatorProfile` bio, location, audience notes, and account names.
2. Posts and videos: Instagram media captions and YouTube video titles and descriptions.
3. Transcripts: `YouTubeTranscript.text` where status is available. Transcripts are the strongest signal for a tutorial such as “Building AI Agents with LangGraph.”
4. Retrieved subset: embed those texts, search Qdrant for the chunks that best match the taxonomy decision, and send that subset to the LLM with the allowed IAB IDs.

Example outcome for that video, after a human accepts it:

| Level | Value | Source |
| --- | --- | --- |
| IAB category | `597` Artificial Intelligence | Official 3.1 row. Parent `596` Technology & Computing |
| Niche | Tech | Existing `Category` |
| Sub-niche | Artificial Intelligence | Custom or starter subcategory, mapped to `597` |
| Micro-niche | AI Agent Development | Custom only. No IAB ID |

If the same creator also posts gadget reviews, add a second result (for example Computing / consumer electronics) instead of overwriting the AI label.

Rules in `classify_with_rules()` can stay as a fallback when there is no transcript and no API key. They match keywords on `Category` and `Subcategory`. They are not a substitute for the IAB ID check.

## 4. Test 50–100 creators before a full run

Pick creators that already have Instagram or YouTube rows, including some with transcripts and some with only a bio. Score each creator three ways:

1. Profile only (current `profile_text()` path).
2. Profile plus post captions, video titles, descriptions, and available transcripts.
3. The same content after Qdrant retrieval, constrained to imported IAB IDs, then mapped to niche / sub-niche / micro-niche.

Have a person mark each niche and sub-niche correct or incorrect. Record whether the IAB ID is a real row and whether the micro-niche is more specific than that row without contradicting it.

Ship the method with the higher niche and sub-niche accuracy. If transcripts are missing for most of the sample, fix sync before judging the LLM.

## 5. Run it

The vendored file is `data/taxonomies/Content Taxonomy 3.1.tsv`.

```powershell
.\.venv\Scripts\python manage.py import_iab_taxonomy
.\.venv\Scripts\python manage.py classify_iab --limit 50
```

`import_iab_taxonomy` loads official rows and the starter mappings whose IDs still match the file. `classify_iab` prints three results per creator: profile only, posts and transcripts, and retrieved content. The creator page Classify action stores the retrieved result. Set `QDRANT_URL` when a Qdrant server should hold the chunk vectors. With that variable empty, retrieval stays in process.

From Python, import the package and call it:

```python
from classify import import_taxonomy, classify

import_taxonomy()
labels = classify(creator)
labels = classify(creator, mode="content")
```

## 6. Rules that keep the labels honest

- Import IDs. Do not type them from memory or from a model response that has not been checked against the table.
- Store `3.1` on every official row and every classification that cites one.
- Allow several IAB categories and several custom niches per creator.
- Put specificity you cannot find in the TSV in the custom micro-niche, and keep the closest official parent ID.
- Re-import when the TSV changes. Do not edit official names in the database to match a campaign brief.

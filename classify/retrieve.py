import hashlib
import logging
import math
import re
import uuid

import requests
from django.conf import settings

logger = logging.getLogger("classify")

VECTOR_SIZE = 32
_TOKEN = re.compile(r"[a-z0-9]+")


def embed_text(text):
    """Stable hashed bag-of-words vector. Used only to retrieve chunks, not to assign a category."""
    vector = [0.0] * VECTOR_SIZE
    for token in _TOKEN.findall((text or "").lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        vector[digest[0] % VECTOR_SIZE] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def retrieve_chunks(creator, chunks, *, limit=8):
    if not chunks:
        return []
    url = getattr(settings, "QDRANT_URL", "")
    if url:
        try:
            return _qdrant_retrieve(creator, chunks, url, limit=limit)
        except requests.RequestException as exc:
            logger.warning("qdrant_retrieve_failed creator_id=%s error=%s", creator.pk, exc.__class__.__name__)
    return lexical_retrieve(chunks, limit=limit)


def lexical_retrieve(chunks, *, limit=8):
    ranked = sorted(chunks, key=lambda chunk: (-chunk.get("score", 0), -len(chunk.get("text") or "")))
    chosen = [chunk for chunk in ranked if chunk.get("score", 0) > 0][:limit]
    if chosen:
        return chosen
    return ranked[:limit]


def _qdrant_retrieve(creator, chunks, url, *, limit):
    collection = getattr(settings, "QDRANT_COLLECTION", "creator_content")
    headers = {"content-type": "application/json"}
    api_key = getattr(settings, "QDRANT_API_KEY", "")
    if api_key:
        headers["api-key"] = api_key
    created = requests.put(
        f"{url}/collections/{collection}",
        headers=headers,
        json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}},
        timeout=8,
    )
    if created.status_code not in (200, 409):
        created.raise_for_status()
    points = []
    for chunk in chunks:
        points.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{creator.pk}:{chunk['ref']}")),
                "vector": embed_text(chunk["text"]),
                "payload": {
                    "creator_id": creator.pk,
                    "ref": chunk["ref"],
                    "source": chunk["source"],
                    "text": chunk["text"][:4000],
                },
            }
        )
    requests.put(
        f"{url}/collections/{collection}/points",
        headers=headers,
        json={"points": points},
        timeout=20,
    ).raise_for_status()
    query = embed_text("\n".join(chunk["text"] for chunk in chunks[:4]))
    response = requests.post(
        f"{url}/collections/{collection}/points/search",
        headers=headers,
        json={
            "vector": query,
            "limit": limit,
            "filter": {"must": [{"key": "creator_id", "match": {"value": creator.pk}}]},
            "with_payload": True,
        },
        timeout=15,
    )
    response.raise_for_status()
    found = []
    by_ref = {chunk["ref"]: chunk for chunk in chunks}
    for hit in response.json().get("result") or []:
        payload = hit.get("payload") or {}
        chunk = by_ref.get(payload.get("ref"))
        if chunk is not None:
            found.append(chunk)
    return found or lexical_retrieve(chunks, limit=limit)

import hashlib
import re

from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags

from networks.youtube.client import YouTubeAPIError
from networks.youtube.parse import clip
from networks.youtube_models import YouTubeTranscript

_VTT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
)


def content_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def parse_vtt(payload):
    segments = []
    lines = (payload or "").replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines):
        match = _VTT_TIME.search(lines[index])
        if not match:
            index += 1
            continue
        index += 1
        text_lines = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(strip_tags(lines[index]).strip())
            index += 1
        text = " ".join(part for part in text_lines if part)
        if text:
            segments.append(
                {
                    "start": _clock_seconds(match.group("start")),
                    "end": _clock_seconds(match.group("end")),
                    "text": text,
                }
            )
    transcript = "\n".join(segment["text"] for segment in segments)
    return transcript, segments


def _clock_seconds(value):
    hours, minutes, seconds = value.split(":")
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def scopes_allow_captions(scopes):
    if not scopes:
        return None
    text = scopes.lower()
    return "youtube.force-ssl" in text or "youtubepartner" in text


class YouTubeCaptionProvider:
    name = "youtube_captions"

    def fetch(self, client, video):
        payload = client.captions(video.video_id)
        tracks = payload.get("items") or []
        if not tracks:
            return []
        results = []
        for track in tracks:
            snippet = track.get("snippet") or {}
            track_id = str(track.get("id") or "")
            language = snippet.get("language") or "und"
            meta = {
                "language": language,
                "track_id": track_id,
                "track_name": clip(snippet.get("name") or "", 255),
                "caption_kind": snippet.get("trackKind") or "",
                "source": self.name,
            }
            if not track_id:
                results.append({**meta, "status": YouTubeTranscript.Status.UNAVAILABLE, "failure_reason": "Caption track has no id."})
                continue
            try:
                body = client.caption_download(track_id)
            except YouTubeAPIError as exc:
                status = (
                    YouTubeTranscript.Status.RESTRICTED
                    if exc.kind == "permission"
                    else YouTubeTranscript.Status.FAILED
                )
                results.append({**meta, "status": status, "failure_reason": str(exc)})
                continue
            text, segments = parse_vtt(body)
            if not text:
                results.append(
                    {
                        **meta,
                        "status": YouTubeTranscript.Status.UNAVAILABLE,
                        "failure_reason": "The caption track did not include text.",
                        "text": "",
                        "segments": [],
                    }
                )
                continue
            results.append({**meta, "status": YouTubeTranscript.Status.AVAILABLE, "text": text, "segments": segments})
        return results


class ConfiguredTranscriptProvider:
    name = "configured_provider"

    def configured(self):
        return bool(getattr(settings, "YOUTUBE_TRANSCRIPT_PROVIDER_URL", ""))

    def fetch(self, video):
        import requests

        url = settings.YOUTUBE_TRANSCRIPT_PROVIDER_URL
        headers = {"Accept": "application/json"}
        token = getattr(settings, "YOUTUBE_TRANSCRIPT_PROVIDER_TOKEN", "") or ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.get(
                url,
                params={"video_id": video.video_id},
                headers=headers,
                timeout=float(getattr(settings, "YOUTUBE_HTTP_TIMEOUT", 25) or 25),
            )
        except requests.RequestException as exc:
            raise YouTubeAPIError("The configured transcript provider could not be reached.", kind="failed") from exc
        if response.status_code in {401, 403}:
            raise YouTubeAPIError("The configured transcript provider refused the request.", kind="permission")
        if response.status_code >= 400:
            raise YouTubeAPIError("The configured transcript provider returned an error.", kind="failed")
        try:
            payload = response.json()
        except ValueError as exc:
            raise YouTubeAPIError("The configured transcript provider did not return JSON.", kind="failed") from exc
        if not isinstance(payload, dict):
            raise YouTubeAPIError("The configured transcript provider returned an unexpected response.", kind="failed")
        text = payload.get("text") or ""
        if not text and payload.get("segments"):
            text = "\n".join(str(item.get("text") or "") for item in payload["segments"] if isinstance(item, dict))
        return {
            "language": payload.get("language") or video.default_language or "und",
            "track_id": str(payload.get("track_id") or "external"),
            "track_name": clip(payload.get("track_name") or "Configured provider", 255),
            "caption_kind": payload.get("caption_kind") or "",
            "source": self.name,
            "status": YouTubeTranscript.Status.AVAILABLE if text else YouTubeTranscript.Status.UNAVAILABLE,
            "text": text,
            "segments": payload.get("segments") if isinstance(payload.get("segments"), list) else [],
            "failure_reason": "" if text else "The configured provider did not return transcript text.",
        }


def store_transcript(video, result):
    now = timezone.now()
    language = result.get("language") or "und"
    track_id = result.get("track_id") or ""
    source = result.get("source") or "unknown"
    text = result.get("text") or ""
    digest = content_hash(text) if text else ""
    row, _created = YouTubeTranscript.objects.get_or_create(
        video=video,
        language=language,
        track_id=track_id,
        source=source,
        defaults={
            "track_name": result.get("track_name") or "",
            "caption_kind": result.get("caption_kind") or "",
            "status": result.get("status") or YouTubeTranscript.Status.FAILED,
            "last_attempt_at": now,
            "failure_reason": result.get("failure_reason") or "",
        },
    )
    row.last_attempt_at = now
    row.track_name = result.get("track_name") or row.track_name
    row.caption_kind = result.get("caption_kind") or row.caption_kind
    status = result.get("status") or YouTubeTranscript.Status.FAILED
    if text and row.content_hash == digest and row.text:
        row.status = YouTubeTranscript.Status.AVAILABLE
        row.last_success_at = now
        row.failure_reason = ""
        row.save(update_fields=["last_attempt_at", "last_success_at", "status", "failure_reason", "track_name", "caption_kind"])
        return row, False
    if text:
        row.text = text
        row.segments = result.get("segments") or []
        row.content_hash = digest
        row.status = YouTubeTranscript.Status.AVAILABLE
        row.last_success_at = now
        row.failure_reason = ""
    else:
        row.status = status
        row.failure_reason = result.get("failure_reason") or ""
        if row.text and status != YouTubeTranscript.Status.AVAILABLE:
            row.status = YouTubeTranscript.Status.AVAILABLE
    row.save()
    return row, bool(text)


def record_status(video, *, language, source, status, reason):
    now = timezone.now()
    row, _created = YouTubeTranscript.objects.get_or_create(
        video=video,
        language=language or "und",
        track_id="",
        source=source,
        defaults={"status": status, "last_attempt_at": now, "failure_reason": reason},
    )
    if row.text and row.status == YouTubeTranscript.Status.AVAILABLE:
        row.last_attempt_at = now
        row.failure_reason = reason
        row.save(update_fields=["last_attempt_at", "failure_reason"])
        return row
    row.status = status
    row.last_attempt_at = now
    row.failure_reason = reason
    row.save(update_fields=["status", "last_attempt_at", "failure_reason"])
    return row

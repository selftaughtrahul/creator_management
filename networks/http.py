import requests

from django.conf import settings


class ProviderError(Exception):
    pass


def _message(response):
    try:
        data = response.json()
    except ValueError:
        return (response.text or "The network returned an empty response.")[:300]
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    if isinstance(error, str):
        return str(data.get("error_description") or error)[:300]
    description = data.get("error_description") or data.get("message")
    if description:
        return str(description)[:300]
    return (response.text or "Request failed.")[:300]


def api_get(url, *, headers=None, params=None):
    try:
        response = requests.get(url, headers=headers, params=params, timeout=25)
    except requests.RequestException as exc:
        raise ProviderError("Could not reach the network. Check the connection and try again.") from exc
    if response.status_code >= 400:
        raise ProviderError(_message(response))
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError("The network returned a response that was not JSON.") from exc


def api_post(url, *, data=None, headers=None):
    try:
        response = requests.post(url, data=data, headers=headers, timeout=25)
    except requests.RequestException as exc:
        raise ProviderError("Could not reach the network. Check the connection and try again.") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("The network returned a response that was not JSON.") from exc
    if response.status_code >= 400 or payload.get("error"):
        if response.status_code >= 400:
            raise ProviderError(_message(response))
        error = payload.get("error")
        if isinstance(error, dict):
            raise ProviderError(str(error.get("message") or error)[:300])
        if error:
            raise ProviderError(str(payload.get("error_description") or error)[:300])
    return payload


def as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _graph_version():
    version = settings.META_GRAPH_VERSION or "v23.0"
    if not version.startswith("v"):
        version = f"v{version}"
    return version


def graph(path):
    return f"https://graph.facebook.com/{_graph_version()}/{path.lstrip('/')}"


def ig_graph(path):
    return f"https://graph.instagram.com/{_graph_version()}/{path.lstrip('/')}"

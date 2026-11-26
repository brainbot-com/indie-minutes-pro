"""This service's own address as people reach it, for settings with
PUBLIC_URL=auto: on an Indie Box the gateway gives the app a port of its own
(9200-9249) on whatever host names and TLS the box is set up with, so the
address is the one the current request came in on. Work without a request (a
meeting's link in the Drive log) uses the last address the gateway forwarded."""
import contextvars

_current = contextvars.ContextVar("indie_recorder_base", default=None)
_last = {"base": "http://localhost:8000"}


def set_from(request):
    _current.set(f"{request.url.scheme}://{request.url.netloc}")


def remember(request):
    """A signed-in request (api.token_row) through the gateway: how people reach us,
    for work without a request. Not just any request: its Host header is the
    sender's to choose. An app on the box calling http://indie-minutes-pro:8000
    directly does not count either."""
    if request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-host"):
        _last["base"] = f"{request.url.scheme}://{request.url.netloc}"


def current():
    return _current.get() or _last["base"]

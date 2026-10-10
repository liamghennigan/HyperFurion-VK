"""A local server is reached directly.

A local address says where HyperFurion VK connects, so the request must
really go there: requests would otherwise hand it to HTTP(S)_PROXY or
ALL_PROXY (and, on Windows and macOS, the system proxy), and follow a
redirect to wherever the server points. For an endpoint that
config._is_local_endpoint accepts, the speech, language-model and embedding
clients send with every proxy switched off and redirects refused. Online
endpoints keep requests' normal behaviour.
"""

from __future__ import annotations

import requests

# "all" too: requests' env lookup also returns ALL_PROXY under that key, and
# a None for http/https alone would still let it through.
NO_PROXIES = {"http": None, "https": None, "all": None}


def request_kwargs(direct: bool) -> dict:
    """Extra keyword arguments for requests.post / Session.post."""
    if not direct:
        return {}
    return {"proxies": dict(NO_PROXIES), "allow_redirects": False}


def refuse_redirect(response, direct: bool) -> None:
    """A local server answering with a redirect is an error, not a hop."""
    if not direct:
        return
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool) and 300 <= status < 400:
        headers = getattr(response, "headers", None) or {}
        where = headers.get("location", "") if hasattr(headers, "get") else ""
        raise requests.RequestException(
            f"the local server answered with a redirect ({status}"
            + (f" to {where}" if where else "")
            + "); HyperFurion VK doesn't follow redirects from a local server"
        )

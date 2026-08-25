"""Small hardened JSON HTTP client for inventory providers."""

import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from app.services.outbound_security import secure_transport_enforced, validate_outbound_url, OutboundSecurityError
from app.services.url_credentials import URLCredentialError, authentication_headers, url_credential_details


class HTTPJSONError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPJSONError("Outbound API redirects are not permitted.")


def _opener(verify_tls, proxy_url=None):
    if not verify_tls and secure_transport_enforced():
        raise HTTPJSONError("TLS certificate verification cannot be disabled.")
    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    handlers = [_NoRedirect(), HTTPSHandler(context=context)]
    if proxy_url:
        handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return build_opener(*handlers)


def credential_base_url(credential):
    try:
        _username, data = url_credential_details(credential)
        return data["url"].rstrip("/")
    except URLCredentialError as exc:
        raise HTTPJSONError(str(exc)) from exc


def get_json(credential, path_or_url, *, verify_tls=True, timeout=30, headers=None, proxy_url=None):
    base_url = credential_base_url(credential)
    target = str(path_or_url or "").strip()
    if not target:
        target = base_url
    elif not target.startswith(("http://", "https://")):
        target = urljoin(base_url + "/", target.lstrip("/"))
    try:
        target = validate_outbound_url(target, purpose="Inventory API")
        auth_headers = authentication_headers(credential, verify_tls=verify_tls, timeout=timeout, proxy_url=proxy_url)
    except (OutboundSecurityError, URLCredentialError) as exc:
        raise HTTPJSONError(str(exc)) from exc
    request_headers = {"Accept": "application/json", **auth_headers, **(headers or {})}
    request = Request(target, headers=request_headers, method="GET")
    try:
        with _opener(verify_tls, proxy_url=proxy_url).open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise HTTPJSONError("Inventory API returned HTTP {}.".format(exc.code)) from exc
    except (URLError, OSError) as exc:
        raise HTTPJSONError("Unable to reach inventory API.") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPJSONError("Inventory API returned invalid JSON.") from exc
    return payload

"""Generic outbound URL/API credential helpers.

The URL credential deliberately separates a remote service endpoint from the
provider using it. Providers such as Satellite, Zabbix, NetBox and Red Hat
Lightspeed can therefore share one credential model while retaining their own
inventory-specific configuration.
"""

import base64
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from app.credential_types import CREDENTIAL_TYPE_URL
from app.services.outbound_security import (
    OutboundSecurityError,
    secure_transport_enforced,
    validate_outbound_url,
)


URL_AUTH_NONE = "none"
URL_AUTH_BASIC = "basic"
URL_AUTH_BEARER = "bearer"
URL_AUTH_TOKEN = "token"
URL_AUTH_OAUTH2_CLIENT_CREDENTIALS = "oauth2_client_credentials"

VALID_URL_AUTH_MODES = frozenset(
    {
        URL_AUTH_NONE,
        URL_AUTH_BASIC,
        URL_AUTH_BEARER,
        URL_AUTH_TOKEN,
        URL_AUTH_OAUTH2_CLIENT_CREDENTIALS,
    }
)


class URLCredentialError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise URLCredentialError("Outbound API redirects are not permitted.")


def normalise_url_credential_data(data, *, username=""):
    data = dict(data or {})
    try:
        data["url"] = validate_outbound_url(
            str(data.get("url") or "").strip().rstrip("/"),
            purpose="URL credential",
            require_https=False,
        )
    except OutboundSecurityError as exc:
        raise URLCredentialError(str(exc)) from exc

    auth_mode = str(data.get("auth_mode") or URL_AUTH_NONE).strip().lower()
    if auth_mode not in VALID_URL_AUTH_MODES:
        raise URLCredentialError("A valid URL credential authentication mode is required.")
    data["auth_mode"] = auth_mode

    token_prefix = str(data.get("token_prefix") or "").strip()
    if auth_mode == URL_AUTH_BEARER and not token_prefix:
        token_prefix = "Bearer"
    elif auth_mode == URL_AUTH_TOKEN and not token_prefix:
        token_prefix = "Token"
    data["token_prefix"] = token_prefix

    if auth_mode == URL_AUTH_BASIC:
        if not str(username or "").strip():
            raise URLCredentialError("Username is required for basic URL authentication.")
        if not data.get("password"):
            raise URLCredentialError("Password is required for basic URL authentication.")
    elif auth_mode in {URL_AUTH_BEARER, URL_AUTH_TOKEN}:
        if not data.get("token"):
            raise URLCredentialError("Token is required for token-based URL authentication.")
    elif auth_mode == URL_AUTH_OAUTH2_CLIENT_CREDENTIALS:
        if not str(username or "").strip():
            raise URLCredentialError("Client ID is required for OAuth client-credentials authentication.")
        if not data.get("password"):
            raise URLCredentialError("Client secret is required for OAuth client-credentials authentication.")
        token_url = str(data.get("token_url") or "").strip()
        if not token_url:
            raise URLCredentialError("OAuth token URL is required.")
        try:
            data["token_url"] = validate_outbound_url(token_url, purpose="OAuth token endpoint")
        except OutboundSecurityError as exc:
            raise URLCredentialError(str(exc)) from exc
        data["scope"] = str(data.get("scope") or "").strip()

    return data


def url_credential_details(credential, *, require_https=True):
    if credential is None or credential.credential_type != CREDENTIAL_TYPE_URL:
        raise URLCredentialError("A URL / API credential is required.")
    try:
        data = credential.get_credential_data()
    except Exception as exc:
        raise URLCredentialError("Unable to decrypt URL credential.") from exc
    data = normalise_url_credential_data(data, username=credential.username)
    if require_https and urlsplit(data["url"]).scheme.lower() != "https":
        raise URLCredentialError("URL credential must use https://.")
    try:
        data["url"] = validate_outbound_url(
            data["url"],
            purpose="URL credential",
            require_https=require_https,
        )
    except OutboundSecurityError as exc:
        raise URLCredentialError(str(exc)) from exc
    return credential.username or "", data


def _ssl_context(verify_tls):
    if not verify_tls and secure_transport_enforced():
        raise URLCredentialError("TLS certificate verification cannot be disabled.")
    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _opener(verify_tls, proxy_url=None):
    handlers = [_NoRedirect(), HTTPSHandler(context=_ssl_context(verify_tls))]
    if proxy_url:
        handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return build_opener(*handlers)


def proxy_url_for_credential(credential):
    """Return a urllib proxy URL from a URL/API credential.

    Proxy credentials deliberately reuse the encrypted URL/API credential
    model. Only unauthenticated and HTTP Basic modes are meaningful for an
    HTTP proxy; bearer/token/OAuth modes are rejected.
    """
    username, data = url_credential_details(credential, require_https=False)
    mode = data.get("auth_mode")
    if mode not in {URL_AUTH_NONE, URL_AUTH_BASIC}:
        raise URLCredentialError(
            "A proxy URL / API credential must use None or HTTP Basic authentication."
        )
    try:
        proxy = validate_outbound_url(
            data["url"], purpose="Inventory proxy", require_https=False
        )
    except OutboundSecurityError as exc:
        raise URLCredentialError(str(exc)) from exc
    if mode == URL_AUTH_NONE:
        return proxy
    parts = urlsplit(proxy)
    user = quote(str(username or ""), safe="")
    password = quote(str(data.get("password") or ""), safe="")
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = "[{}]".format(host)
    if parts.port is not None:
        host = "{}:{}".format(host, parts.port)
    netloc = "{}:{}@{}".format(user, password, host)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _oauth_access_token(*, username, data, verify_tls=True, timeout=30, proxy_url=None):
    body = {
        "grant_type": "client_credentials",
        "client_id": username,
        "client_secret": data.get("password") or "",
    }
    if data.get("scope"):
        body["scope"] = data["scope"]
    request = Request(
        data["token_url"],
        data=urlencode(body).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with _opener(verify_tls, proxy_url=proxy_url).open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise URLCredentialError("Unable to obtain OAuth access token.") from exc
    token = str(payload.get("access_token") or "").strip() if isinstance(payload, dict) else ""
    if not token:
        raise URLCredentialError("OAuth token endpoint did not return an access token.")
    return token


def authentication_headers(credential, *, verify_tls=True, timeout=30, proxy_url=None):
    username, data = url_credential_details(credential)
    mode = data["auth_mode"]
    if mode == URL_AUTH_NONE:
        return {}
    if mode == URL_AUTH_BASIC:
        raw = "{}:{}".format(username, data.get("password") or "").encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
    if mode in {URL_AUTH_BEARER, URL_AUTH_TOKEN}:
        prefix = data.get("token_prefix") or ("Bearer" if mode == URL_AUTH_BEARER else "Token")
        return {"Authorization": "{} {}".format(prefix, data.get("token") or "").strip()}
    token = _oauth_access_token(
        username=username,
        data=data,
        verify_tls=verify_tls,
        timeout=timeout,
        proxy_url=proxy_url,
    )
    return {"Authorization": "Bearer {}".format(token)}

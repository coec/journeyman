import ssl
import urllib.error
import urllib.parse
import urllib.request

from app import db
from app.credential_crypto import decrypt_credential_data, encrypt_credential_data
from app.models import EnvironmentBuildSetting
from app.services.url_credentials import URLCredentialError, proxy_url_for_credential, url_credential_details
from app.services.outbound_security import OutboundSecurityError, validate_outbound_url


class EnvironmentBuildSettingsError(ValueError):
    pass


def get_or_create_environment_build_settings():
    settings = db.session.get(EnvironmentBuildSetting, 1)
    if settings is None:
        settings = EnvironmentBuildSetting(id=1)
        db.session.add(settings)
        db.session.commit()
    return settings


def settings_to_form_data(settings):
    return {
        "proxy_enabled": settings.proxy_enabled,
        "proxy_url": settings.proxy_url,
        "proxy_username": settings.proxy_username,
        "proxy_password": "",
        "has_proxy_password": settings.has_proxy_password(),
        "no_proxy": settings.no_proxy,
    }


def form_data(form):
    return {
        "proxy_enabled": form.get("proxy_enabled") == "on",
        "proxy_url": str(form.get("proxy_url") or "").strip(),
        "proxy_username": str(form.get("proxy_username") or "").strip(),
        "proxy_password": str(form.get("proxy_password") or ""),
        "has_proxy_password": form.get("has_proxy_password") == "1",
        "no_proxy": str(form.get("no_proxy") or "").strip(),
    }


def validate(values):
    errors = []
    url = values["proxy_url"]
    if values["proxy_enabled"]:
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            parsed = None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            errors.append("Proxy URL must be a valid http:// or https:// URL.")
        if parsed and (parsed.username or parsed.password):
            errors.append("Store proxy username and password in their separate fields, not in the URL.")
        try:
            validate_outbound_url(
                url, purpose="Environment-build proxy", require_https=False
            )
        except OutboundSecurityError as exc:
            errors.append(str(exc))
        if values["proxy_username"] and not (values["proxy_password"] or values["has_proxy_password"]):
            errors.append("A proxy password is required when a proxy username is configured.")
    if errors:
        raise EnvironmentBuildSettingsError(" ".join(errors))
    return values


def update(settings, values, username):
    settings.proxy_enabled = values["proxy_enabled"]
    settings.proxy_url = values["proxy_url"]
    settings.proxy_username = values["proxy_username"]
    settings.no_proxy = values["no_proxy"]
    settings.updated_by = username
    if values["proxy_password"]:
        settings.encrypted_proxy_password = encrypt_credential_data({"password": values["proxy_password"]})
    elif not values["has_proxy_password"] or not values["proxy_username"]:
        settings.encrypted_proxy_password = None
    db.session.commit()


def _password(settings):
    if not settings.encrypted_proxy_password:
        return ""
    return str(decrypt_credential_data(settings.encrypted_proxy_password).get("password") or "")


def proxy_url_with_credentials(settings):
    if not settings.proxy_enabled:
        return ""
    try:
        validated_url = validate_outbound_url(
            settings.proxy_url,
            purpose="Environment-build proxy",
            require_https=False,
        )
    except OutboundSecurityError as exc:
        raise EnvironmentBuildSettingsError(str(exc)) from exc
    parsed = urllib.parse.urlsplit(validated_url)
    username = settings.proxy_username
    password = _password(settings)
    if username:
        auth = urllib.parse.quote(username, safe="")
        if password:
            auth += ":" + urllib.parse.quote(password, safe="")
        netloc = auth + "@" + (parsed.hostname or "")
        if parsed.port:
            netloc += ":" + str(parsed.port)
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return settings.proxy_url


def build_proxy_environment(base_environment, *, proxy_credential=None):
    settings = get_or_create_environment_build_settings()
    result = dict(base_environment)
    if proxy_credential is not None:
        try:
            proxy = proxy_url_for_credential(proxy_credential)
        except URLCredentialError as exc:
            raise EnvironmentBuildSettingsError(str(exc)) from exc
    else:
        if not settings.proxy_enabled:
            return result
        proxy = proxy_url_with_credentials(settings)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        result[key] = proxy
    for key in ("NO_PROXY", "no_proxy"):
        result[key] = settings.no_proxy
    return result


def redact_proxy_secrets(text, *, proxy_credential=None):
    settings = get_or_create_environment_build_settings()
    output = str(text or "")
    password = _password(settings)
    try:
        authenticated = proxy_url_with_credentials(settings)
    except EnvironmentBuildSettingsError:
        # Redaction is a safety net for command output and must never turn an
        # otherwise successful command into an application error.  The raw
        # password is still redacted below even if the stored proxy URL has
        # become invalid.
        authenticated = ""
    values = [password, authenticated]
    if proxy_credential is not None:
        try:
            credential_proxy = proxy_url_for_credential(proxy_credential)
            _username, credential_data = url_credential_details(
                proxy_credential, require_https=False
            )
            values.extend([credential_proxy, str(credential_data.get("password") or "")])
        except Exception:
            # Redaction must remain best-effort and must never hide the original
            # build/validation result behind a credential-processing exception.
            pass
    for value in values:
        if value:
            output = output.replace(value, "[redacted]")
    return output


def test_proxy():
    settings = get_or_create_environment_build_settings()
    if not settings.proxy_enabled:
        raise EnvironmentBuildSettingsError("Environment-build proxy is not enabled.")
    proxy = proxy_url_with_credentials(settings)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    results = []
    for label, url in (("PyPI", "https://pypi.org/simple/ansible-core/"), ("Ansible Galaxy", "https://galaxy.ansible.com/api/")):
        try:
            with opener.open(url, timeout=15) as response:
                results.append(f"{label}: HTTP {response.status}")
        except (urllib.error.URLError, OSError) as exc:
            raise EnvironmentBuildSettingsError(redact_proxy_secrets(f"{label} test failed: {exc}")) from exc
    return "; ".join(results)

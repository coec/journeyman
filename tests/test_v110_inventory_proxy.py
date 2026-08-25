from app.services.url_credentials import (
    URLCredentialError,
    normalise_url_credential_data,
    proxy_url_for_credential,
    url_credential_details,
)


class DummyURLCredential:
    credential_type = "url"

    def __init__(self, username, data):
        self.username = username
        self._data = data

    def get_credential_data(self):
        return dict(self._data)


def test_proxy_url_credential_none_auth():
    credential = DummyURLCredential("", {
        "url": "http://proxy.example.com:8080",
        "auth_mode": "none",
    })
    assert proxy_url_for_credential(credential) == "http://proxy.example.com:8080"


def test_proxy_url_credential_basic_auth_encodes_secrets():
    credential = DummyURLCredential("user@domain", {
        "url": "http://proxy.example.com:8080",
        "auth_mode": "basic",
        "password": "p@ss:word",
    })
    assert proxy_url_for_credential(credential) == (
        "http://user%40domain:p%40ss%3Aword@proxy.example.com:8080"
    )


def test_proxy_url_credential_rejects_bearer_auth():
    credential = DummyURLCredential("", {
        "url": "https://proxy.example.com",
        "auth_mode": "bearer",
        "token": "secret",
    })
    try:
        proxy_url_for_credential(credential)
    except URLCredentialError as exc:
        assert "None or HTTP Basic" in str(exc)
    else:
        raise AssertionError("Bearer proxy credential was accepted")


def test_http_url_credential_can_be_stored_for_proxy_use():
    data = normalise_url_credential_data({
        "url": "http://proxy.example.com:8080",
        "auth_mode": "none",
    })
    assert data["url"] == "http://proxy.example.com:8080"


def test_http_url_credential_is_still_rejected_for_normal_api_use():
    credential = DummyURLCredential("", {
        "url": "http://api.example.com",
        "auth_mode": "none",
    })
    try:
        url_credential_details(credential)
    except URLCredentialError as exc:
        assert "must use https://" in str(exc)
    else:
        raise AssertionError("HTTP API credential was accepted for normal API use")

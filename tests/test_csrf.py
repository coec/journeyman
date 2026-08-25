import html
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.security


PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)


FORM_PATTERN = re.compile(
    r"<form\b(?P<attributes>[^>]*)>"
    r"(?P<body>.*?)</form>",
    flags=(
        re.IGNORECASE
        | re.DOTALL
    ),
)

POST_METHOD_PATTERN = re.compile(
    r'''\bmethod\s*=\s*["']post["']''',
    flags=re.IGNORECASE,
)

CSRF_FIELD_PATTERN = re.compile(
    r'''\bname\s*=\s*["']csrf_token["']''',
    flags=re.IGNORECASE,
)

CSRF_META_PATTERN = re.compile(
    r'''
    <meta
    \s+
    name=["']csrf-token["']
    \s+
    content=["']([^"']+)["']
    \s*/?>
    ''',
    flags=(
        re.IGNORECASE
        | re.VERBOSE
    ),
)


def identity_headers(
    username,
    groups=(),
):
    headers = {
        "X-Test-Username": username,
    }

    if groups:
        headers["X-Test-Groups"] = (
            ",".join(groups)
        )

    return headers


def extract_csrf_token(response):
    body = response.get_data(
        as_text=True
    )

    match = CSRF_META_PATTERN.search(
        body
    )

    assert match is not None, (
        "Rendered page does not contain "
        "the CSRF meta token."
    )

    return html.unescape(
        match.group(1)
    )


@pytest.fixture
def csrf_app(app):
    original_value = app.config.get(
        "WTF_CSRF_ENABLED",
        True,
    )

    app.config[
        "WTF_CSRF_ENABLED"
    ] = True

    yield app

    app.config[
        "WTF_CSRF_ENABLED"
    ] = original_value


@pytest.fixture
def csrf_client(csrf_app):
    return csrf_app.test_client()


def test_every_post_form_contains_csrf_field():
    templates_root = (
        PROJECT_ROOT
        / "app"
        / "templates"
    )

    post_forms = []
    missing_fields = []

    for path in sorted(
        templates_root.glob("*.html")
    ):
        text = path.read_text(
            encoding="utf-8"
        )

        for form_number, match in enumerate(
            FORM_PATTERN.finditer(text),
            start=1,
        ):
            if not POST_METHOD_PATTERN.search(
                match.group("attributes")
            ):
                continue

            identity = (
                "{} form {}".format(
                    path.relative_to(
                        PROJECT_ROOT
                    ),
                    form_number,
                )
            )

            post_forms.append(identity)

            if not CSRF_FIELD_PATTERN.search(
                match.group("body")
            ):
                missing_fields.append(
                    identity
                )

    assert post_forms
    assert missing_fields == []


def test_get_request_renders_csrf_meta_token(
    csrf_client,
    seeded_packages,
):
    response = csrf_client.get(
        "/packages",
        headers=identity_headers(
            "outsider"
        ),
    )

    assert response.status_code == 200
    assert extract_csrf_token(response)


def test_post_without_csrf_token_is_rejected(
    csrf_client,
    seeded_packages,
):
    response = csrf_client.post(
        "/packages/{}/launch".format(
            seeded_packages[
                "authenticated_package"
            ]
        ),
        data={},
        headers=identity_headers(
            "outsider"
        ),
    )

    assert response.status_code == 400

    body = response.get_data(
        as_text=True
    )

    assert (
        "Request could not be verified"
        in body
    )

    content_security_policy = response.headers.get(
        "Content-Security-Policy",
        "",
    )
    assert "script-src 'self' 'nonce-" in content_security_policy
    assert "style-src 'self' 'nonce-" in content_security_policy
    assert "'unsafe-inline'" not in content_security_policy


def test_post_with_valid_csrf_token_reaches_route(
    csrf_client,
    seeded_packages,
):
    headers = identity_headers(
        "outsider"
    )

    page = csrf_client.get(
        "/packages",
        headers=headers,
    )

    token = extract_csrf_token(
        page
    )

    response = csrf_client.post(
        "/packages/{}/launch".format(
            seeded_packages[
                "authenticated_package"
            ]
        ),
        data={
            "csrf_token": token,
        },
        headers=headers,
    )

    assert response.status_code == 200

    assert (
        "Review Package Dispatch"
        in response.get_data(
            as_text=True
        )
    )


def test_tampered_csrf_token_is_rejected(
    csrf_client,
    seeded_packages,
):
    headers = identity_headers(
        "outsider"
    )

    page = csrf_client.get(
        "/packages",
        headers=headers,
    )

    token = extract_csrf_token(
        page
    )

    token_parts = token.rsplit(
        ".",
        1,
    )

    assert len(token_parts) == 2
    assert token_parts[1]

    signature = token_parts[1]

    replacement = (
        "A"
        if signature[0] != "A"
        else "B"
    )

    tampered_signature = (
        replacement
        + signature[1:]
    )

    tampered_token = (
        token_parts[0]
        + "."
        + tampered_signature
    )

    response = csrf_client.post(
        "/packages/{}/launch".format(
            seeded_packages[
                "authenticated_package"
            ]
        ),
        data={
            "csrf_token": tampered_token,
        },
        headers=headers,
    )

    assert response.status_code == 400


def test_csrf_token_cannot_cross_browser_sessions(
    csrf_app,
    seeded_packages,
):
    first_client = (
        csrf_app.test_client()
    )

    second_client = (
        csrf_app.test_client()
    )

    headers = identity_headers(
        "outsider"
    )

    page = first_client.get(
        "/packages",
        headers=headers,
    )

    first_client_token = (
        extract_csrf_token(page)
    )

    response = second_client.post(
        "/packages/{}/launch".format(
            seeded_packages[
                "authenticated_package"
            ]
        ),
        data={
            "csrf_token": (
                first_client_token
            ),
        },
        headers=headers,
    )

    assert response.status_code == 400


def test_valid_csrf_token_does_not_bypass_authorisation(
    csrf_client,
    seeded_packages,
):
    headers = identity_headers(
        "outsider"
    )

    page = csrf_client.get(
        "/packages",
        headers=headers,
    )

    token = extract_csrf_token(
        page
    )

    response = csrf_client.post(
        "/packages/{}/delete".format(
            seeded_packages[
                "user_package"
            ]
        ),
        data={
            "csrf_token": token,
        },
        headers=headers,
    )

    assert response.status_code == 403

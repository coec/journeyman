import html
import re

import pytest

from app import db
from app.models import (
    Job,
    ProjectPackage,
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


def response_text(response):
    return response.get_data(
        as_text=True
    )


def extract_launch_token(response):
    body = response_text(
        response
    )

    match = re.search(
        (
            r'name="launch_token"\s+'
            r'value="([^"]+)"'
        ),
        body,
        flags=re.DOTALL,
    )

    assert match is not None, (
        "Package preview did not contain "
        "a launch token."
    )

    return html.unescape(
        match.group(1)
    )


def package_launch_form(
    seeded_packages,
    *,
    device="switch01",
    password="secret-route-value-483920",
):
    return {
        "package_value_{}".format(
            seeded_packages[
                "user_device_input"
            ]
        ): device,
        "package_value_{}".format(
            seeded_packages[
                "user_password_input"
            ]
        ): password,
    }


def create_user_package_preview(
    client,
    seeded_packages,
    *,
    username="alice",
    device="switch01",
    password="secret-route-value-483920",
):
    response = client.post(
        "/packages/{}/launch".format(
            seeded_packages[
                "user_package"
            ]
        ),
        data=package_launch_form(
            seeded_packages,
            device=device,
            password=password,
        ),
        headers=identity_headers(
            username
        ),
    )

    assert response.status_code == 200

    return response


@pytest.mark.parametrize(
    (
        "username",
        "groups",
        "expected_names",
        "forbidden_names",
    ),
    [
        (
            "alice",
            (),
            {
                "User Restricted Package",
                "Authenticated Package",
            },
            {
                "Group Restricted Package",
                "Disabled Package",
                "Disabled Project Package",
            },
        ),
        (
            "operator",
            (
                "Network Operators",
            ),
            {
                "Group Restricted Package",
                "Authenticated Package",
            },
            {
                "User Restricted Package",
                "Disabled Package",
                "Disabled Project Package",
            },
        ),
        (
            "outsider",
            (),
            {
                "Authenticated Package",
            },
            {
                "User Restricted Package",
                "Group Restricted Package",
                "Disabled Package",
                "Disabled Project Package",
            },
        ),
        (
            "admin",
            (),
            {
                "User Restricted Package",
                "Group Restricted Package",
                "Authenticated Package",
                "Disabled Package",
                "Disabled Project Package",
            },
            set(),
        ),
    ],
)
def test_packages_list_is_permission_filtered(
    client,
    seeded_packages,
    username,
    groups,
    expected_names,
    forbidden_names,
):
    response = client.get(
        "/packages",
        headers=identity_headers(
            username,
            groups,
        ),
    )

    assert response.status_code == 200

    body = response_text(
        response
    )

    for package_name in expected_names:
        assert package_name in body

    for package_name in forbidden_names:
        assert package_name not in body


@pytest.mark.parametrize(
    (
        "username",
        "groups",
        "package_key",
        "expected_status",
    ),
    [
        (
            "alice",
            (),
            "user_package",
            200,
        ),
        (
            "operator",
            (
                "Network Operators",
            ),
            "group_package",
            200,
        ),
        (
            "outsider",
            (),
            "user_package",
            403,
        ),
        (
            "admin",
            (),
            "user_package",
            200,
        ),
        (
            "admin",
            (),
            "disabled_package",
            403,
        ),
        (
            "outsider",
            (),
            "authenticated_package",
            200,
        ),
    ],
)
def test_package_launch_get_enforces_permissions(
    client,
    seeded_packages,
    username,
    groups,
    package_key,
    expected_status,
):
    response = client.get(
        "/packages/{}/launch".format(
            seeded_packages[
                package_key
            ]
        ),
        headers=identity_headers(
            username,
            groups,
        ),
    )

    assert (
        response.status_code
        == expected_status
    )



def test_step_limit_input_offers_effective_inventory_hosts(
    app,
    client,
    seeded_packages,
):
    with app.app_context():
        package = db.session.get(
            ProjectPackage,
            seeded_packages["user_package"],
        )
        device_input = next(
            item
            for item in package.inputs
            if item.id == seeded_packages["user_device_input"]
        )
        device_input.binding_type = "step_limit"
        db.session.commit()

    response = client.get(
        "/packages/{}/launch".format(
            seeded_packages["user_package"]
        ),
        headers=identity_headers("alice"),
    )

    assert response.status_code == 200
    body = response_text(response)
    assert 'list="package-step-limit-hosts"' in body
    assert '<datalist id="package-step-limit-hosts">' in body
    assert 'value="execution-host.example"' in body
    assert app.test_preview_calls[-1]["step_limit_override"] == ""
    assert app.test_preview_calls[-1]["refresh_inventory_sources"] is False

def test_unauthorised_user_cannot_post_package_values(
    app,
    client,
    seeded_packages,
):
    secret_value = (
        "unauthorised-secret-482012"
    )

    response = client.post(
        "/packages/{}/launch".format(
            seeded_packages[
                "user_package"
            ]
        ),
        data=package_launch_form(
            seeded_packages,
            password=secret_value,
        ),
        headers=identity_headers(
            "outsider"
        ),
    )

    assert response.status_code == 403
    assert secret_value not in response_text(
        response
    )

    assert (
        app.test_preview_calls
        == []
    )

    assert (
        app.test_queue_calls
        == []
    )


def test_package_administration_routes_remain_admin_only(
    client,
    seeded_packages,
):
    non_admin_headers = identity_headers(
        "alice"
    )

    assert client.get(
        "/packages/new",
        headers=non_admin_headers,
    ).status_code == 403

    assert client.get(
        "/packages/{}/edit".format(
            seeded_packages[
                "user_package"
            ]
        ),
        headers=non_admin_headers,
    ).status_code == 403

    assert client.post(
        "/packages/{}/delete".format(
            seeded_packages[
                "user_package"
            ]
        ),
        headers=non_admin_headers,
    ).status_code == 403

    assert client.get(
        "/packages/new",
        headers=identity_headers(
            "admin"
        ),
    ).status_code == 200


def test_package_preview_warns_when_filtered_targets_may_change(
    client,
    app,
    seeded_packages,
):
    app.test_preview_state.update(
        {
            "inventory_type": "filtered",
            "refresh_affects_filtered_targets": True,
            "target_hosts_may_change": True,
        }
    )

    response = create_user_package_preview(
        client,
        seeded_packages,
    )
    body = response_text(response)

    assert "Inventory targets may change during execution" in body
    assert "Refreshes inventory for later filtered steps" in body
    assert "Target list may change after inventory refresh" in body
    assert "Filtered" in body


def test_package_preview_refreshes_inventory_sources_once_before_review(
    client,
    app,
    seeded_packages,
):
    create_user_package_preview(
        client,
        seeded_packages,
    )

    assert app.test_preview_calls[-1][
        "refresh_inventory_sources"
    ] is True


def test_package_launch_preview_redacts_secret(
    client,
    seeded_packages,
):
    secret_value = (
        "preview-secret-957231"
    )

    response = create_user_package_preview(
        client,
        seeded_packages,
        device="switch01",
        password=secret_value,
    )

    body = response_text(
        response
    )

    assert "Review Package Dispatch" in body
    assert "Operational Targets" in body
    assert "switch01" in body

    assert (
        "execution-host.example"
        in body
    )

    assert secret_value not in body

    token = extract_launch_token(
        response
    )

    assert token
    assert secret_value not in token


def test_package_launch_rejects_missing_required_value(
    client,
    seeded_packages,
):
    secret_value = (
        "validation-secret-195023"
    )

    response = client.post(
        "/packages/{}/launch".format(
            seeded_packages[
                "user_package"
            ]
        ),
        data={
            "package_value_{}".format(
                seeded_packages[
                    "user_password_input"
                ]
            ): secret_value,
        },
        headers=identity_headers(
            "alice"
        ),
    )

    assert response.status_code == 400

    body = response_text(
        response
    )

    assert "Device is required." in body
    assert secret_value not in body

    assert (
        'name="launch_token"'
        not in body
    )


def test_confirmed_package_launch_queues_job_and_protects_job(
    app,
    client,
    seeded_packages,
):
    device = "switch01"
    secret_value = (
        "queued-secret-837490"
    )

    preview_response = (
        create_user_package_preview(
            client,
            seeded_packages,
            device=device,
            password=secret_value,
        )
    )

    launch_token = extract_launch_token(
        preview_response
    )

    queue_response = client.post(
        "/packages/{}/run".format(
            seeded_packages[
                "user_package"
            ]
        ),
        data={
            "launch_token": launch_token,
            "confirm_targets": "yes",
        },
        headers=identity_headers(
            "alice"
        ),
        follow_redirects=False,
    )

    assert queue_response.status_code == 302

    location = queue_response.headers[
        "Location"
    ]

    match = re.search(
        r"/jobs/(\d+)$",
        location,
    )

    assert match is not None

    job_id = int(
        match.group(1)
    )

    assert len(
        app.test_queue_calls
    ) == 1

    queue_call = (
        app.test_queue_calls[0]
    )

    execution_data = (
        queue_call[
            "package_execution"
        ]
    )

    assert (
        queue_call["requested_by"]
        == "alice"
    )

    assert (
        execution_data.execution_vars[
            "device"
        ]
        == device
    )

    assert (
        execution_data.execution_vars[
            "password"
        ]
        == secret_value
    )

    assert (
        execution_data.operational_targets
        == [device]
    )

    assert secret_value not in repr(
        execution_data.display_values
    )

    with app.app_context():
        job = db.session.get(
            Job,
            job_id,
        )

        assert job is not None
        assert job.requested_by == "alice"
        assert job.package_snapshot is not None

        snapshot = job.package_snapshot

        assert (
            secret_value.encode("utf-8")
            not in snapshot.encrypted_extra_vars
        )

        assert (
            secret_value
            not in snapshot.package_definition_json
        )

        assert (
            secret_value
            not in snapshot.display_values_json
        )

        assert (
            secret_value
            not in snapshot.operational_targets_json
        )

        assert (
            snapshot.get_execution_vars()[
                "password"
            ]
            == secret_value
        )

    owner_response = client.get(
        "/jobs/{}".format(job_id),
        headers=identity_headers(
            "alice"
        ),
    )

    assert owner_response.status_code == 200

    owner_body = response_text(
        owner_response
    )

    assert "User Restricted Package" in owner_body
    assert device in owner_body
    assert secret_value not in owner_body

    other_user_response = client.get(
        "/jobs/{}".format(job_id),
        headers=identity_headers(
            "bob"
        ),
    )

    assert (
        other_user_response.status_code
        == 403
    )

    administrator_response = client.get(
        "/jobs/{}".format(job_id),
        headers=identity_headers(
            "admin"
        ),
    )

    assert (
        administrator_response.status_code
        == 200
    )


def test_launch_token_is_bound_to_requesting_user(
    app,
    client,
    seeded_packages,
):
    package_id = seeded_packages[
        "authenticated_package"
    ]

    preview_response = client.post(
        "/packages/{}/launch".format(
            package_id
        ),
        data={},
        headers=identity_headers(
            "alice"
        ),
    )

    assert preview_response.status_code == 200

    launch_token = extract_launch_token(
        preview_response
    )

    response = client.post(
        "/packages/{}/run".format(
            package_id
        ),
        data={
            "launch_token": launch_token,
            "confirm_targets": "yes",
        },
        headers=identity_headers(
            "bob"
        ),
        follow_redirects=True,
    )

    assert response.status_code == 200

    assert (
        "belongs to another user"
        in response_text(response)
    )

    assert (
        app.test_queue_calls
        == []
    )


def test_package_definition_change_invalidates_preview(
    app,
    client,
    seeded_packages,
):
    package_id = seeded_packages[
        "authenticated_package"
    ]

    preview_response = client.post(
        "/packages/{}/launch".format(
            package_id
        ),
        data={},
        headers=identity_headers(
            "alice"
        ),
    )

    launch_token = extract_launch_token(
        preview_response
    )

    with app.app_context():
        package = db.session.get(
            ProjectPackage,
            package_id,
        )

        package.warning_message = (
            "The Package definition changed."
        )

        db.session.commit()

    response = client.post(
        "/packages/{}/run".format(
            package_id
        ),
        data={
            "launch_token": launch_token,
            "confirm_targets": "yes",
        },
        headers=identity_headers(
            "alice"
        ),
        follow_redirects=True,
    )

    assert response.status_code == 200

    assert (
        "definition changed after it was reviewed"
        in response_text(response)
    )

    assert (
        app.test_queue_calls
        == []
    )


def test_inventory_change_forces_fresh_preview(
    app,
    client,
    seeded_packages,
):
    package_id = seeded_packages[
        "authenticated_package"
    ]

    preview_response = client.post(
        "/packages/{}/launch".format(
            package_id
        ),
        data={},
        headers=identity_headers(
            "alice"
        ),
    )

    launch_token = extract_launch_token(
        preview_response
    )

    app.test_preview_state[
        "digest"
    ] = "preview-digest-v2"

    response = client.post(
        "/packages/{}/run".format(
            package_id
        ),
        data={
            "launch_token": launch_token,
            "confirm_targets": "yes",
        },
        headers=identity_headers(
            "alice"
        ),
        follow_redirects=False,
    )

    assert response.status_code == 409

    body = response_text(
        response
    )

    assert (
        "resolved inventory changed"
        in body
    )

    assert (
        "execution-host.example"
        in body
    )

    assert (
        app.test_queue_calls
        == []
    )

    refreshed_token = extract_launch_token(
        response
    )

    assert refreshed_token
    assert refreshed_token != launch_token


def test_package_notifications_links_back_to_package_edit(
    client,
    seeded_packages,
):
    package_id = seeded_packages["user_package"]

    response = client.get(
        f"/packages/{package_id}/notifications",
        headers=identity_headers("admin"),
    )

    assert response.status_code == 200
    body = response_text(response)
    assert f'href="/packages/{package_id}/edit"' in body

"""Cross-cutting authorization invariants used as ASVS V8 evidence."""

from types import SimpleNamespace

import pytest
from flask import Flask, g

from app.auth import can_cancel_job, can_view_job
from app.security_scope import (
    SECURITY_SCOPE_PRIVATE,
    SECURITY_SCOPE_PUBLIC,
    SECURITY_SCOPE_SHARED,
    can_manage_scoped_object,
    can_reveal_scoped_object_secret,
    can_use_scoped_object,
)

pytestmark = pytest.mark.security


def test_security_scopes_separate_use_from_management():
    owner = "alice"

    assert can_use_scoped_object(owner, SECURITY_SCOPE_PRIVATE, "alice")
    assert not can_use_scoped_object(owner, SECURITY_SCOPE_PRIVATE, "bob")
    assert can_use_scoped_object(
        owner, SECURITY_SCOPE_PRIVATE, "bob", is_admin=True
    )

    # Explicit generic sharing is not implemented yet, so shared intentionally
    # retains private semantics rather than accidentally broadening access.
    assert not can_use_scoped_object(owner, SECURITY_SCOPE_SHARED, "bob")

    assert can_use_scoped_object(owner, SECURITY_SCOPE_PUBLIC, "bob")
    assert not can_manage_scoped_object(owner, "bob")
    assert can_manage_scoped_object(owner, "alice")
    assert can_manage_scoped_object(owner, "bob", is_admin=True)


def test_secret_reveal_is_field_level_owner_only_even_for_admin():
    assert can_reveal_scoped_object_secret("alice", "alice")
    assert not can_reveal_scoped_object_secret("alice", "bob")
    # The helper intentionally has no administrator bypass. Credential route
    # tests verify this behaviour through the HTTP endpoint as well.
    assert not can_reveal_scoped_object_secret("alice", "admin")


def test_job_object_authorization_is_bound_to_requesting_subject():
    app = Flask(__name__)
    job = SimpleNamespace(requested_by="alice")

    with app.test_request_context():
        g.authenticated_username = "alice"
        g.authenticated_role = "User"
        assert can_view_job(job)
        assert can_cancel_job(job)

        g.authenticated_username = "bob"
        assert not can_view_job(job)
        assert not can_cancel_job(job)

        g.authenticated_role = "Administrator"
        assert can_view_job(job)
        assert can_cancel_job(job)

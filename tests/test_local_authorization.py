from app import db
from app.models import AuthorizationRight, AuthorizationRole, UserAccount
from app.services.authorization import (
    RIGHT_APPROVER,
    RIGHT_REVIEWER,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_AUTOMATION_ADMIN,
    ROLE_RESOURCE_ADMIN,
    ROLE_USER,
    ensure_builtin_authorization_definitions,
    sync_legacy_directory_user,
    user_has_right,
    user_has_role,
)


def test_builtin_authorization_catalogue_exists_after_migration(app):
    with app.app_context():
        assert {row.name for row in AuthorizationRole.query.all()} >= {
            ROLE_ADMIN,
            ROLE_USER,
            ROLE_AUDITOR,
        }
        assert {row.name for row in AuthorizationRight.query.all()} >= {
            RIGHT_REVIEWER,
            RIGHT_APPROVER,
        }


def test_legacy_directory_user_is_created_with_user_role(app):
    with app.app_context():
        account = sync_legacy_directory_user(
            username="alice",
            display_name="Alice Example",
            directory_object_guid="11111111-1111-1111-1111-111111111111",
            legacy_role="User",
        )

        assert account.username == "alice"
        assert account.display_name == "Alice Example"
        assert account.has_role(ROLE_USER)
        assert not account.has_role(ROLE_ADMIN)
        assert user_has_role("ALICE", ROLE_USER)


def test_legacy_directory_admin_is_seeded_as_admin_and_user(app):
    with app.app_context():
        account = sync_legacy_directory_user(
            username="admin.user",
            display_name="Admin User",
            directory_object_guid="22222222-2222-2222-2222-222222222222",
            legacy_role="Administrator",
        )

        assert account.has_role(ROLE_ADMIN)
        assert account.has_role(ROLE_AUTOMATION_ADMIN)
        assert account.has_role(ROLE_RESOURCE_ADMIN)
        assert account.has_role(ROLE_USER)


def test_sync_does_not_change_locally_assigned_roles_or_rights(app):
    with app.app_context():
        account = sync_legacy_directory_user(
            username="reviewer",
            display_name="Reviewer",
            directory_object_guid="33333333-3333-3333-3333-333333333333",
            legacy_role="User",
        )
        roles, rights = ensure_builtin_authorization_definitions()
        account.roles.append(roles[ROLE_AUDITOR])
        account.rights.extend([rights[RIGHT_REVIEWER], rights[RIGHT_APPROVER]])
        db.session.commit()

        account = sync_legacy_directory_user(
            username="reviewer",
            display_name="Reviewer Renamed",
            directory_object_guid="33333333-3333-3333-3333-333333333333",
            legacy_role="User",
        )

        assert account.display_name == "Reviewer Renamed"
        assert account.has_role(ROLE_USER)
        assert account.has_role(ROLE_AUDITOR)
        assert account.has_right(RIGHT_REVIEWER)
        assert account.has_right(RIGHT_APPROVER)
        assert user_has_right("reviewer", RIGHT_REVIEWER)


def test_disabled_local_user_has_no_effective_local_role_or_right(app):
    with app.app_context():
        account = sync_legacy_directory_user(
            username="disabled.user",
            directory_object_guid="44444444-4444-4444-4444-444444444444",
            legacy_role="Administrator",
        )
        _, rights = ensure_builtin_authorization_definitions()
        account.rights.append(rights[RIGHT_REVIEWER])
        account.enabled = False
        db.session.commit()

        assert not user_has_role("disabled.user", ROLE_ADMIN)
        assert not user_has_right("disabled.user", RIGHT_REVIEWER)


def test_existing_local_authorization_is_not_reseeded_from_directory(app):
    with app.app_context():
        account = sync_legacy_directory_user(
            username="former.admin",
            directory_object_guid="55555555-5555-5555-5555-555555555555",
            legacy_role="Administrator",
        )
        admin_role = AuthorizationRole.query.filter_by(name=ROLE_ADMIN).one()
        account.roles.remove(admin_role)
        db.session.commit()

        account = sync_legacy_directory_user(
            username="former.admin",
            directory_object_guid="55555555-5555-5555-5555-555555555555",
            legacy_role="Administrator",
        )

        assert account.has_role(ROLE_USER)
        assert not account.has_role(ROLE_ADMIN)


def test_directory_sync_does_not_reenable_disabled_existing_account(app):
    with app.app_context():
        account = sync_legacy_directory_user(
            username="disabled.again",
            directory_object_guid="66666666-6666-6666-6666-666666666666",
            legacy_role="User",
        )
        account.enabled = False
        db.session.commit()

        account = sync_legacy_directory_user(
            username="disabled.again",
            directory_object_guid="66666666-6666-6666-6666-666666666666",
            legacy_role="Administrator",
        )

        assert account.enabled is False
        assert not account.has_role(ROLE_ADMIN)


def test_builtin_role_catalogue_includes_v2_admin_split(app):
    from app.services.authorization import (
        ROLE_AUTOMATION_ADMIN,
        ROLE_RESOURCE_ADMIN,
        ensure_builtin_authorization_definitions,
    )

    with app.app_context():
        roles, _rights = ensure_builtin_authorization_definitions()
        assert set(roles) >= {
            ROLE_ADMIN,
            ROLE_AUTOMATION_ADMIN,
            ROLE_RESOURCE_ADMIN,
            ROLE_USER,
            ROLE_AUDITOR,
        }


def test_admin_roles_imply_user_server_side(app):
    from app.services.authorization import (
        ROLE_AUTOMATION_ADMIN,
        ROLE_RESOURCE_ADMIN,
        create_user_account,
    )

    with app.app_context():
        for username, role_name in (
            ("platform.admin", ROLE_ADMIN),
            ("automation.admin", ROLE_AUTOMATION_ADMIN),
            ("resource.admin", ROLE_RESOURCE_ADMIN),
        ):
            account = create_user_account(
                username=username,
                role_names=[role_name],
            )
            assert account.has_role(role_name)
            assert account.has_role(ROLE_USER)


def test_auditor_does_not_imply_user(app):
    from app.services.authorization import create_user_account

    with app.app_context():
        account = create_user_account(
            username="audit.only",
            role_names=[ROLE_AUDITOR],
        )
        assert account.has_role(ROLE_AUDITOR)
        assert not account.has_role(ROLE_USER)

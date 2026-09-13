from datetime import datetime, timezone

from app import db
from app.models import Project, ProjectSchedule
from app.services.name_ordering import reserved_name_ordering
from app.services.schedules import calculate_next_run


def test_interval_schedule_calculates_next_boundary(app):
    with app.app_context():
        schedule = ProjectSchedule(
            project_id=1,
            name="Every hour",
            schedule_type="interval",
            timezone_name="UTC",
            start_at=datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
            interval_minutes=60,
            weekdays="",
            enabled=True,
            created_by="tester",
        )
        assert calculate_next_run(
            schedule,
            after=datetime(2026, 8, 7, 2, 10, tzinfo=timezone.utc),
        ) == datetime(2026, 8, 7, 3, 0, tzinfo=timezone.utc)


def test_daily_schedule_preserves_local_time(app):
    with app.app_context():
        schedule = ProjectSchedule(
            project_id=1,
            name="Perth morning",
            schedule_type="daily",
            timezone_name="Australia/Perth",
            start_at=datetime(2026, 8, 7, 0, 30, tzinfo=timezone.utc),
            interval_minutes=None,
            weekdays="",
            enabled=True,
            created_by="tester",
        )
        assert calculate_next_run(
            schedule,
            after=datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc),
        ) == datetime(2026, 8, 8, 0, 30, tzinfo=timezone.utc)


def test_daily_schedule_with_far_future_start_uses_start_date(app):
    with app.app_context():
        schedule = ProjectSchedule(
            project_id=1,
            name="Far future daily",
            schedule_type="daily",
            timezone_name="UTC",
            start_at=datetime(2099, 1, 1, 2, 0, tzinfo=timezone.utc),
            interval_minutes=None,
            weekdays="",
            enabled=True,
            created_by="tester",
        )
        assert calculate_next_run(
            schedule,
            after=datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
        ) == datetime(2099, 1, 1, 2, 0, tzinfo=timezone.utc)


def test_interval_schedule_stops_after_end_at(app):
    with app.app_context():
        schedule = ProjectSchedule(
            project_id=1,
            name="Limited interval",
            schedule_type="interval",
            timezone_name="UTC",
            start_at=datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 7, 2, 0, tzinfo=timezone.utc),
            interval_minutes=60,
            weekdays="",
            enabled=True,
            created_by="tester",
        )
        assert calculate_next_run(
            schedule,
            after=datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc),
        ) == datetime(2026, 8, 7, 2, 0, tzinfo=timezone.utc)
        assert calculate_next_run(
            schedule,
            after=datetime(2026, 8, 7, 2, 0, tzinfo=timezone.utc),
        ) is None


def test_daily_schedule_stops_after_end_at(app):
    with app.app_context():
        schedule = ProjectSchedule(
            project_id=1,
            name="Limited daily",
            schedule_type="daily",
            timezone_name="Australia/Perth",
            start_at=datetime(2026, 8, 7, 0, 30, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 8, 0, 30, tzinfo=timezone.utc),
            interval_minutes=None,
            weekdays="",
            enabled=True,
            created_by="tester",
        )
        assert calculate_next_run(
            schedule,
            after=datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc),
        ) == datetime(2026, 8, 8, 0, 30, tzinfo=timezone.utc)
        assert calculate_next_run(
            schedule,
            after=datetime(2026, 8, 8, 0, 30, tzinfo=timezone.utc),
        ) is None


def test_schedule_order_puts_reserved_zz_names_last(app):
    with app.app_context():
        project = Project(
            name="Schedule ordering test",
            description="",
            enabled=True,
            owner="tester",
            security_scope="private",
        )
        db.session.add(project)
        db.session.flush()

        for name in [
            "ZZ - Built-in backup",
            "Nightly patching",
            "Alpha schedule",
        ]:
            db.session.add(ProjectSchedule(
                project_id=project.id,
                name=name,
                schedule_type="daily",
                timezone_name="UTC",
                start_at=datetime(2026, 8, 7, 5, 0, tzinfo=timezone.utc),
                interval_minutes=None,
                weekdays="",
                enabled=False,
                created_by="tester",
            ))
        db.session.commit()

        names = [
            row.name
            for row in (
                ProjectSchedule.query
                .filter_by(project_id=project.id)
                .order_by(*reserved_name_ordering(ProjectSchedule.name))
                .all()
            )
        ]
        assert names == [
            "Alpha schedule",
            "Nightly patching",
            "ZZ - Built-in backup",
        ]


def test_package_schedule_target_properties(app):
    from app.models import ProjectPackage

    with app.app_context():
        project = Project(
            name="Package schedule project",
            description="",
            enabled=True,
            owner="tester",
            security_scope="private",
        )
        package = ProjectPackage(
            name="Package schedule target",
            project=project,
            enabled=True,
            owner="tester",
        )
        db.session.add_all([project, package])
        db.session.flush()

        schedule = ProjectSchedule(
            package_id=package.id,
            name="Scheduled package",
            schedule_type="daily",
            timezone_name="UTC",
            start_at=datetime(2099, 1, 1, 2, 0, tzinfo=timezone.utc),
            weekdays="",
            enabled=True,
            created_by="tester",
        )
        db.session.add(schedule)
        db.session.commit()

        assert schedule.project_id is None
        assert schedule.package_id == package.id
        assert schedule.target_type == "package"
        assert schedule.target_name == package.name
        assert schedule.target_project.id == project.id


def test_schedule_configuration_accepts_package_target(app):
    from app.models import ProjectPackage
    from app.services.schedule_configuration import configure_schedule

    with app.app_context():
        project = Project(
            name="Package configuration project",
            description="",
            enabled=True,
            owner="tester",
            security_scope="private",
        )
        package = ProjectPackage(
            name="Schedulable Package",
            project=project,
            enabled=True,
            owner="tester",
        )
        db.session.add_all([project, package])
        db.session.commit()

        result = configure_schedule({
            "name": "Nightly Package",
            "package": package.name,
            "schedule_type": "daily",
            "timezone": "UTC",
            "start_at": "2099-01-01T02:00",
            "enabled": True,
        }, created_by="api-admin")

        assert result.changed is True
        assert result.schedule.package_id == package.id
        assert result.schedule.project_id is None
        assert result.schedule.target_type == "package"


def test_package_schedule_rejects_required_input_without_default(app):
    from app.models import ProjectPackage, ProjectPackageInput
    from app.services.project_package_launch import (
        PackageLaunchError,
        prepare_package_scheduled_launch,
    )

    with app.app_context():
        project = Project(
            name="Prompted package project",
            description="",
            enabled=True,
            owner="tester",
            security_scope="private",
        )
        package = ProjectPackage(
            name="Prompted Package",
            project=project,
            enabled=True,
            owner="tester",
        )
        package.inputs.append(ProjectPackageInput(
            position=1,
            variable_name="required_value",
            label="Required value",
            input_type="text",
            required=True,
        ))
        db.session.add_all([project, package])
        db.session.commit()

        import pytest
        with pytest.raises(PackageLaunchError, match="cannot run unattended"):
            prepare_package_scheduled_launch(package)


def test_package_schedule_accepts_required_saved_input(app):
    from app.models import ProjectPackage, ProjectPackageInput
    from app.services.project_package_launch import prepare_package_scheduled_launch

    with app.app_context():
        project = Project(
            name="Prompted scheduled project",
            description="",
            enabled=True,
            owner="tester",
            security_scope="private",
        )
        package = ProjectPackage(
            name="Prompted scheduled Package",
            project=project,
            enabled=True,
            owner="tester",
        )
        package_input = ProjectPackageInput(
            position=1,
            variable_name="required_value",
            label="Required value",
            input_type="text",
            required=True,
        )
        package.inputs.append(package_input)
        db.session.add_all([project, package])
        db.session.commit()

        prepared = prepare_package_scheduled_launch(
            package,
            {"package_value_{}".format(package_input.id): "scheduled-value"},
        )
        assert prepared.execution_data.execution_vars["required_value"] == "scheduled-value"


def test_schedule_encrypts_package_answers(app):
    with app.app_context():
        schedule = ProjectSchedule(
            project_id=1,
            name="Encrypted Package answers",
            schedule_type="daily",
            timezone_name="UTC",
            start_at=datetime(2099, 1, 1, 2, 0, tzinfo=timezone.utc),
            weekdays="",
            enabled=False,
            created_by="tester",
        )
        schedule.set_package_answers({"package_value_123": "secret-ish-value"})

        assert schedule.encrypted_package_answers
        assert b"secret-ish-value" not in schedule.encrypted_package_answers
        assert schedule.get_package_answers() == {
            "package_value_123": "secret-ish-value"
        }

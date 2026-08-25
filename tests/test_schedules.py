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

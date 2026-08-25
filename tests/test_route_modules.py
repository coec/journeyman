"""Regression tests for routes extracted from the legacy routes module."""


def test_extracted_routes_keep_existing_endpoint_names(app):
    expected = {
        "main.runners": "/runners",
        "main.runner_create": "/runners/new",
        "main.runner_register_api": "/api/runners/register",
        "main.runner_heartbeat_api": "/api/runners/heartbeat",
        "main.system_status": "/system-status",
        "main.audit_log": "/audit-log",
        "main.environment_build_settings": "/settings/environment-builds",
        "main.system_settings": "/settings",
        "main.data_retention_settings": "/settings/data-retention",
        "main.release_testing_settings": "/settings/release-testing",
        "main.apply_system_settings": "/settings/apply",
        "main.credentials": "/credentials",
        "main.credential_new": "/credentials/new",
        "main.environments": "/environments",
        "main.environment_create_managed": "/environments/create",
        "main.inventories": "/inventories",
        "main.inventory_new": "/inventories/new",
        "main.repositories": "/repositories",
        "main.repository_new": "/repositories/new",
        "main.packages": "/packages",
        "main.project_package_new": "/packages/new",
        "main.projects": "/projects",
        "main.project_new": "/projects/new",
        "main.jobs": "/jobs",
        "main.jobs_events": "/jobs/events",
        "main.job_detail": "/jobs/<int:job_id>",
        "main.job_rerun": "/jobs/<int:job_id>/rerun",
        "main.job_events": "/jobs/<int:job_id>/events",
        "main.dashboard_events": "/dashboard/events",
        "main.audit_log_events": "/audit-log/events",
        "main.audit_log_latest_id": "/audit-log/latest-id",
        "main.sources": "/sources",
        "main.signals": "/signals",
        "main.reactors": "/reactors",
        "main.reactions": "/reactions",
        "main.zabbix_signal_api": "/api/signals/zabbix",
        "main.runner_signal_api": "/api/runners/signals",
    }

    rules = {
        rule.endpoint: rule.rule
        for rule in app.url_map.iter_rules()
    }

    for endpoint, path in expected.items():
        assert rules[endpoint] == path

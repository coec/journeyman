def test_runners_page_patches_heartbeat_state_without_replacing_rows(client):
    response = client.get(
        "/runners",
        headers={"X-Test-Username": "admin"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'addEventListener("runner-update"' in html
    assert 'addEventListener("runner-refresh"' in html
    assert "updateRunnerLiveState" in html
    assert 'data-runner-id=' in html
    assert "runner-live-heartbeat" in html
    assert "runner-live-capacity" in html
    assert "runner-live-load" in html


def test_runners_page_defers_structural_refresh_while_action_menu_is_open(client):
    response = client.get(
        "/runners",
        headers={"X-Test-Username": "admin"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "runnerActionMenuOpen" in html
    assert "refreshPending = true" in html
    assert 'addEventListener("toggle"' in html


def test_runner_events_send_live_payload_without_requesting_panel_refresh(client):
    response = client.get(
        "/runners/events",
        headers={"X-Test-Username": "admin"},
        buffered=False,
    )

    first_event = next(response.response).decode("utf-8")
    assert "event: runner-update" in first_event
    assert '"runners":' in first_event
    assert "event: runner-refresh" not in first_event
    response.close()

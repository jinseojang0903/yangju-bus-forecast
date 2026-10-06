import uuid

from fastapi.testclient import TestClient

from tests.api.helpers import API, DESTINATION, STATION, assert_error


def test_explanation_is_fallback_for_snapshot(client: TestClient) -> None:
    snapshot = client.get(
        f"{API}/snapshot", params={"station": STATION, "destination": DESTINATION}
    ).json()

    response = client.get(f"{API}/snapshot/{snapshot['snapshotId']}/explanation")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshotId"] == snapshot["snapshotId"]
    assert body["computedAt"] == snapshot["computedAt"]
    assert body["source"] == "fallback"
    assert body["fallbackReason"] == "llm_disabled"
    assert body["text"]
    assert set(body) == {"snapshotId", "computedAt", "source", "fallbackReason", "text"}


def test_explanation_for_scenario_snapshot(client: TestClient) -> None:
    snapshot = client.get(
        f"{API}/snapshot",
        params={"station": STATION, "destination": DESTINATION, "scenario": "status_stale"},
    ).json()

    response = client.get(f"{API}/snapshot/{snapshot['snapshotId']}/explanation")

    assert response.status_code == 200
    assert response.json()["computedAt"] == snapshot["computedAt"]


def test_explanation_unknown_snapshot_is_404(client: TestClient) -> None:
    assert_error(client.get(f"{API}/snapshot/{uuid.uuid4()}/explanation"), 404, "NOT_FOUND")


def test_explanation_bad_id_is_400(client: TestClient) -> None:
    body = assert_error(
        client.get(f"{API}/snapshot/not-a-uuid/explanation"), 400, "VALIDATION_FAILED"
    )

    assert body["error"]["details"] == [{"field": "snapshotId", "reason": "uuid 형식이어야 합니다"}]

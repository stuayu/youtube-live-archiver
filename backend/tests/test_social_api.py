def test_misskey_miauth_routes_create_poll_and_cancel_authorization(client, monkeypatch):
    from app.api import social as social_api

    calls = []
    monkeypatch.setattr(social_api.social_service, "start_misskey_auth", lambda url: {
        "flow_id": "a" * 32,
        "authorization_url": "https://misskey.example/miauth/session-id?permission=write%3Anotes",
        "expires_in": 600,
    })
    monkeypatch.setattr(social_api.social_service, "check_misskey_auth", lambda db, flow_id: {
        "state": "connected", "settings": {"accounts": []},
    })
    monkeypatch.setattr(social_api.social_service, "cancel_misskey_auth", lambda flow_id: calls.append(flow_id) or True)

    started = client.post("/api/settings/social/misskey/miauth", json={
        "instance_url": "https://misskey.example",
    })
    manual_token = client.put("/api/settings/social/misskey", json={
        "instance_url": "https://misskey.example", "token": "must-not-be-accepted",
    })
    checked = client.post("/api/settings/social/misskey/miauth/" + "a" * 32 + "/check")
    cancelled = client.delete("/api/settings/social/misskey/miauth/" + "a" * 32)

    assert started.status_code == 200
    assert started.json()["flow_id"] == "a" * 32
    assert manual_token.status_code == 405
    assert checked.status_code == 200
    assert checked.json() == {"state": "connected", "settings": {"accounts": []}}
    assert "token" not in checked.text
    assert cancelled.status_code == 200
    assert cancelled.json() == {"cancelled": True}
    assert calls == ["a" * 32]


def test_ytdlp_settings_are_saved_and_manual_update_can_be_forced(client, monkeypatch):
    from app.services import ytdlp_update

    update_calls = []
    monkeypatch.setattr(ytdlp_update, "trigger_update", lambda channel, force=False: update_calls.append((channel, force)) or True)

    saved = client.put("/api/settings/ytdlp", json={"channel": "nightly", "auto_update": True})
    current = client.get("/api/settings/ytdlp")
    updated = client.post("/api/settings/ytdlp/update", json={"force": True})

    assert saved.status_code == 200
    assert saved.json()["channel"] == "nightly"
    assert saved.json()["auto_update"] is True
    assert current.status_code == 200
    assert current.json()["channel"] == "nightly"
    assert current.json()["auto_update"] is True
    assert updated.status_code == 200
    assert updated.json()["started"] is True
    assert update_calls == [("nightly", True)]

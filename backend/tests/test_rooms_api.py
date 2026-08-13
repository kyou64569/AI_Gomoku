from app.services.room_service import get_game, get_room


def _make_config_and_player(client):
    cfg = client.post(
        "/api/configs/",
        json={"name": "TestCfg", "base_url": "https://api.example.com/v1", "api_key": ""},
    ).json()
    player = client.post(
        "/api/players/",
        json={"name": "Alpha", "model_config_id": cfg["id"], "model_id": "gpt-4o"},
    ).json()
    return cfg, player


def test_delete_config_blocked_when_referenced(client):
    """删除被 AI 玩家引用的配置必须返回 409，且不产生孤儿玩家/卡死对局。"""
    cfg, player = _make_config_and_player(client)

    resp = client.delete(f"/api/configs/{cfg['id']}")

    assert resp.status_code == 409
    # 配置仍然存在（未被删除）
    ids = [c["id"] for c in client.get("/api/configs/").json()]
    assert cfg["id"] in ids


def test_delete_config_ok_when_unreferenced(client):
    """删除未被引用的配置应通过。"""
    cfg, player = _make_config_and_player(client)
    # 先删除引用它的玩家，再删配置
    del_player = client.delete(f"/api/players/{player['id']}")
    assert del_player.status_code == 200

    resp = client.delete(f"/api/configs/{cfg['id']}")

    assert resp.status_code == 200


def test_room_restart_creates_new_game(client, db_session):
    """核心回归：一局结束后「再来一局」必须开新局而非复用已结束的旧 game_id。"""
    cfg, player = _make_config_and_player(client)
    seats = [
        {"player_id": None, "role": "black"},       # 人类
        {"player_id": player["id"], "role": "white"},  # AI
    ]
    r = client.post("/api/rooms/", json={"mode": "pve", "seats": seats})
    assert r.status_code == 200
    room_id = r.json()["id"]

    # 第一局
    s1 = client.post(f"/api/rooms/{room_id}/start")
    assert s1.status_code == 200
    game_a = s1.json()["game_id"]
    assert game_a is not None

    # 模拟第一局结束：将 game 与 room 状态置为 finished
    g = get_game(db_session, game_a)
    g.status = "finished"
    room = get_room(db_session, room_id)
    room.status = "finished"
    db_session.commit()

    # 再来一局 → 应创建并使用新的 game_id
    s2 = client.post(f"/api/rooms/{room_id}/start")
    assert s2.status_code == 200
    game_b = s2.json()["game_id"]
    assert game_b != game_a

    # 幂等：仍在进行中再次 start 应复用同一局（不重复创建）
    s3 = client.post(f"/api/rooms/{room_id}/start")
    assert s3.status_code == 200
    assert s3.json()["game_id"] == game_b

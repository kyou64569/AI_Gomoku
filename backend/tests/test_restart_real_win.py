"""回归测试：真实对局结束后「再来一局」必须开新局（端到端走真实 move 端点）。

覆盖之前的高危缺陷：Room.status 在对局结束时未同步为 finished，导致
start_room 幂等分支永远命中、返回已结束的旧 game_id，前端无法开新局。
"""
import json
import pytest
from app.services.room_service import get_game, get_room, handle_move
from app.routers import game as game_router


@pytest.fixture
def sync_ai(monkeypatch):
    """让 AI 在请求内同步落子（首个空格），从而能走真实 /move 端点把对局下到终局。"""
    def _ai(game_id, db):
        from app.routers.game import get_seat_by_role, is_human_turn
        g = get_game(db, game_id)
        if not g or g.status != "playing":
            return
        if is_human_turn(db, game_id, g.turn):
            return
        seat = get_seat_by_role(db, game_id, "black" if g.turn == 1 else "white")
        pid = seat.get("player_id") if seat else None
        board = json.loads(g.board)
        for r in range(15):
            for c in range(15):
                if board[r][c] == 0:
                    handle_move(db, game_id, g.turn, r, c, "AI",
                                expected_player_id=pid, forbidden=True)
                    return
    monkeypatch.setattr(game_router, "trigger_ai_if_needed", _ai)


def test_real_endpoint_win_then_restart(client, sync_ai):
    cfg = client.post("/api/configs/", json={"name": "C", "base_url": "https://api.example.com/v1", "api_key": ""}).json()
    player = client.post("/api/players/", json={"name": "A", "model_config_id": cfg["id"], "model_id": "m"}).json()
    room_id = client.post("/api/rooms/", json={"mode": "pve", "seats": [
        {"player_id": None, "role": "black"},
        {"player_id": player["id"], "role": "white"},
    ]}).json()["id"]

    gid = client.post(f"/api/rooms/{room_id}/start").json()["game_id"]

    # 黑(人类)在 column0 下 row1..5，白(AI)在 column1 落子，互不干扰 → 黑五连珠胜
    for i in range(5):
        mv = client.post(f"/api/games/{gid}/move", json={"row": i + 1, "col": 0})
        assert mv.status_code == 200, mv.text
        if client.get(f"/api/games/{gid}/state").json()["status"] != "playing":
            break

    room = next(r for r in client.get("/api/rooms/").json() if r["id"] == room_id)
    assert room["status"] == "finished", "真实对局结束后 Room.status 应为 finished"

    new_gid = client.post(f"/api/rooms/{room_id}/start").json()["game_id"]
    assert new_gid != gid, "再来一局应返回新的 game_id，而非已结束的旧对局"

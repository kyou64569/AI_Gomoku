"""单局导出（GET /api/games/{id}/export）测试：txt 人读 / json 程序读 + 下载头。"""
import json

from app.models import Game, Room


def _make_game(db, **overrides):
    room = Room(mode="pve", seats="[]",
                options=json.dumps({"size": 15, "forbidden": 1, "difficulty": "normal", "first": 1}))
    db.add(room)
    db.commit()
    db.refresh(room)
    fields = dict(
        room_id=room.id, status="finished", winner=1, ended_reason="normal",
        history=json.dumps([
            {"player": 1, "player_name": "黑AI", "row": 7, "col": 7},
            {"player": 2, "player_name": "白AI", "row": 7, "col": 8},
        ]),
        logs=json.dumps(["黑AI 落子 (7,7)", "[黑AI] 引擎: 一步连五 (7,7)"]),
        review=json.dumps({"summary": "黑棋速胜", "turning_point": "第1手即胜",
                           "key_moments": [{"move_no": 1, "player": "黑方", "comment": "开局连五"}]}),
    )
    fields.update(overrides)
    game = Game(**fields)
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


def test_export_txt_contains_all_sections(client, db_session):
    game = _make_game(db_session)
    resp = client.get(f"/api/games/{game.id}/export")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd
    assert f"gomoku-game-{game.id}.txt" in cd  # ASCII 回退名
    assert "filename*=UTF-8''" in cd  # 中文文件名（RFC 5987）
    text = resp.content.decode("utf-8")
    assert "AI Gomoku 对局导出" in text
    assert "黑方 黑AI 胜（连五）" in text  # 结果 + 结束原因
    assert "1. 黑 黑AI  (7,7)" in text  # 落子记录
    assert "[黑AI] 引擎: 一步连五 (7,7)" in text  # 思考日志原文
    assert "AI 复盘点评" in text and "黑棋速胜" in text  # 复盘
    # 时间美化：不含 ISO 的 T 与微秒
    import re
    assert re.search(r"时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text), text[:200]


def test_export_full_board_draw_reason(client, db_session):
    """满盘和棋（ended_reason='full'，room_service 实际写入值）→ 结果行带原因。"""
    game = _make_game(db_session, status="draw", winner=0, ended_reason="full")
    resp = client.get(f"/api/games/{game.id}/export")
    text = resp.content.decode("utf-8")
    assert "结果：平局（满盘和棋）" in text


def test_export_json_structure(client, db_session):
    game = _make_game(db_session)
    resp = client.get(f"/api/games/{game.id}/export?format=json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert data["game"]["id"] == game.id
    assert data["game"]["moves"] == 2
    assert data["game"]["options"]["forbidden"] == 1
    assert data["moves"][0] == {"no": 1, "player": 1, "name": "黑AI", "row": 7, "col": 7}
    assert data["logs"] == ["黑AI 落子 (7,7)", "[黑AI] 引擎: 一步连五 (7,7)"]
    assert data["review"]["summary"] == "黑棋速胜"


def test_export_in_progress_game_exports_partial(client, db_session):
    """进行中的对局也能导出（导出当前已产生的内容），结果文案为「未分出胜负」。"""
    game = _make_game(db_session, status="playing", winner=0, ended_reason="")
    resp = client.get(f"/api/games/{game.id}/export?format=json")
    data = resp.json()
    assert data["game"]["status"] == "playing"
    assert data["game"]["result_text"] == "未分出胜负"  # 区别于终局平局


def test_export_404_and_bad_format(client):
    assert client.get("/api/games/999999/export").status_code == 404
    assert client.get("/api/games/1/export?format=csv").status_code == 422  # 仅允许 txt|json


def test_export_survives_broken_json_fields(client, db_session):
    """history/logs/review 里存了坏 JSON 时导出不炸（历史遗留脏数据兜底）。"""
    game = _make_game(db_session, history="not-json", logs="{bad", review="{also-bad")
    resp = client.get(f"/api/games/{game.id}/export?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["moves"] == [] and data["logs"] == [] and data["review"] is None

import time
from unittest.mock import MagicMock, patch
from app.services.llm_service import _get_client, clear_client_cache, _clients, _clients_lock
from app.routers.configs import validate_url
from app.routers.game import acquire_ai_lock, cleanup_ai_move_lock, ai_move_locks, AI_LOCK_TTL
from app.routers.rooms import _get_room_lock, _cleanup_room_lock, _room_start_locks, _ROOM_LOCKS_MAX_SIZE
from app.services.room_service import _get_move_lock, cleanup_move_lock, _move_locks, _MOVE_LOCKS_MAX_SIZE


def test_client_cache_and_invalidation():
    """测试 OpenAI 客户端缓存复用、容量上限与主动失效"""
    clear_client_cache()
    assert len(_clients) == 0

    c1 = _get_client("https://api.openai.com/v1", "key1")
    c2 = _get_client("https://api.openai.com/v1", "key1")
    assert c1 is c2
    assert len(_clients) == 1

    # 换 key 会创建新 client
    c3 = _get_client("https://api.openai.com/v1", "key2")
    assert c3 is not c1
    assert len(_clients) == 2

    # 按 base_url + key 清理
    clear_client_cache(base_url="https://api.openai.com/v1", api_key="key1")
    assert ("https://api.openai.com/v1", "key1") not in _clients
    assert ("https://api.openai.com/v1", "key2") in _clients

    # 全量清理
    clear_client_cache()
    assert len(_clients) == 0


def test_client_cache_ttl_and_lru():
    """测试客户端缓存过期与容量淘汰"""
    clear_client_cache()
    _get_client("https://api.openai.com/v1", "k1")
    assert len(_clients) == 1

    # 模拟过期
    with _clients_lock:
        _clients[("https://api.openai.com/v1", "k1")]["last_used"] = time.monotonic() - 4000

    # 再次获取时触发惰性过期清理
    c_new = _get_client("https://api.openai.com/v1", "k2")
    assert ("https://api.openai.com/v1", "k1") not in _clients
    assert ("https://api.openai.com/v1", "k2") in _clients
    clear_client_cache()


def test_validate_url_ssrf_protection():
    """测试 SSRF 防护：拦截 IPv6 回环、localhost、私网与保留网段"""
    blocked_urls = [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://0.0.0.0:8000/v1",
        "http://[::1]:8000/v1",
        "http://::1/v1",
        "http://10.0.0.1:8000/v1",
        "http://192.168.1.1:8000/v1",
        "http://172.16.0.1:8000/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://[fe80::1]/v1",
    ]
    for url in blocked_urls:
        assert validate_url(url) is False, f"URL 应被拦截但放行了: {url}"

    # 正常外部域名应放行
    assert validate_url("https://api.openai.com/v1") is True


def test_game_locks_cleanup_and_expiry():
    """测试 game.py 中 AI 移动锁的获取、过期自动清理与手动清理"""
    # 模拟历史过期锁
    ai_move_locks[99991] = time.monotonic() - (AI_LOCK_TTL + 10)
    ai_move_locks[99992] = time.monotonic()

    # acquire 触发过期清理
    ok = acquire_ai_lock(99993)
    assert ok is True
    assert 99991 not in ai_move_locks
    assert 99992 in ai_move_locks
    assert 99993 in ai_move_locks

    # 清理指定游戏锁
    cleanup_ai_move_lock(99993)
    assert 99993 not in ai_move_locks
    cleanup_ai_move_lock(99992)


def test_room_and_move_locks_cleanup():
    """测试 rooms.py 与 room_service.py 锁字典的清理与容量管理"""
    l1 = _get_room_lock(101)
    assert 101 in _room_start_locks
    _cleanup_room_lock(101)
    assert 101 not in _room_start_locks

    m1 = _get_move_lock(201)
    assert 201 in _move_locks
    cleanup_move_lock(201)
    assert 201 not in _move_locks

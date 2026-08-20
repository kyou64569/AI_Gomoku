"""SSE 客户端：观察指定 game 的推送序列（验证 turn 是否正确推给前端）。

用法：python sse_probe.py [game_id] [base_url]
默认：game_id=8，base_url=环境变量 SSE_BASE 或 http://localhost:8000
"""
import httpx, json, time, sys, os

def stream(game_id, base):
    with httpx.stream("GET", f"{base}/api/games/{game_id}/stream", timeout=60) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                stones = sum(1 for row in data.get("board", []) for c in row if c != 0)
                print(f"[{time.strftime('%H:%M:%S')}] push: turn={data.get('turn')} stones={stones} status={data.get('status')} ai_pending={data.get('ai_pending')} logs={(data.get('logs') or [])[-1:]}")
                sys.stdout.flush()

try:
    game_id = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    base = os.environ.get("SSE_BASE", sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000")
    stream(game_id, base)
except Exception as e:
    print("SSE 连接异常:", e)

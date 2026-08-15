"""SSE 客户端：观察 game 8 的推送序列（验证 turn 是否正确推给前端）。"""
import httpx, json, time, sys

def stream():
    with httpx.stream("GET", "http://localhost:8002/api/games/8/stream", timeout=60) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                stones = sum(1 for row in data["board"] for c in row if c != 0)
                print(f"[{time.strftime('%H:%M:%S')}] push: turn={data['turn']} stones={stones} status={data['status']} ai_pending={data.get('ai_pending')} logs={data['logs'][-1:]}")
                sys.stdout.flush()

try:
    stream()
except Exception as e:
    print("SSE 连接异常:", e)

"""前端视角复现：SSE 客户端 + 快速落子，检测前端 turn 与后端不一致的持续时间。

用法：python repro_front.py [BASE_URL]  （默认读取环境变量 REPRO_BASE 或 localhost:8000）
"""
import httpx, json, time, threading, random, os, sys

BASE = os.environ.get("REPRO_BASE", sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")
random.seed(int(os.environ.get("SEED", "7")))
out = []

r = httpx.post(f"{BASE}/api/rooms/", json={"mode": "pve", "seats": [{"role": "black", "player_id": None}, {"role": "white", "player_id": 4}]}, timeout=10).json()
room_id = r["id"]
g = httpx.post(f"{BASE}/api/rooms/{room_id}/start", timeout=10).json()
game_id = g["game_id"]
out.append(f"房间#{room_id} 对局#{game_id}")

# 模拟前端状态：最近收到的 SSE 推送
front_state = {"turn": None, "stones": 0, "ai_pending": False}
lock = threading.Lock()
sse_errors = []

def sse_watch():
    try:
        with httpx.stream("GET", f"{BASE}/api/games/{game_id}/stream", timeout=180) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        d = json.loads(line[6:])
                        if "board" not in d: continue  # game_over 等
                        stones = sum(1 for row in d["board"] for c in row if c != 0)
                        with lock:
                            front_state["turn"] = d["turn"]
                            front_state["stones"] = stones
                            front_state["ai_pending"] = d.get("ai_pending", False)
                    except Exception as e:
                        sse_errors.append(str(e)[:60])
    except Exception as e:
        out.append(f"[SSE] 断开: {str(e)[:80]}")
threading.Thread(target=sse_watch, daemon=True).start()

time.sleep(1.5)
t_start = time.monotonic()
stale_events = []   # 前端 turn 与后端不一致的瞬间
max_stale = 0

for i in range(20):
    # 等 AI 落完（后端 turn=1）
    waited = 0
    while True:
        st = httpx.get(f"{BASE}/api/games/{game_id}/state", timeout=5).json()
        if st["turn"] == 1: break
        waited += 0.2
        if waited > 10:
            out.append(f"!! 第{i+1}手 AI 超时未落 turn={st['turn']}")
            break
        time.sleep(0.2)
    if st["turn"] != 1: break
    # 用户落子
    board = st["board"]
    empties = [(x, y) for x in range(15) for y in range(15) if board[x][y] == 0]
    if not empties: break
    x, y = empties[0]  # 固定顺序，避免随机五连
    mr = httpx.post(f"{BASE}/api/games/{game_id}/move", json={"row": x, "col": y}, timeout=5)
    if mr.status_code != 200:
        out.append(f"!! 第{i+1}手被拒: {mr.status_code} {mr.text[:50]}")
        break
    # 立即检查前端视角（乐观更新后 front 应最终收到 turn=1）
    time.sleep(0.4)
    with lock:
        ft, fs_, fp = front_state["turn"], front_state["stones"], front_state["ai_pending"]
    bs = sum(1 for row in st["board"] for c in row if c != 0) + 1
    # 后端此刻可能 AI 已落（stones 偶数 turn=1）或未落
    st2 = httpx.get(f"{BASE}/api/games/{game_id}/state", timeout=5).json()
    bt = st2["turn"]
    if ft is not None and ft != bt:
        stale_events.append((round(time.monotonic()-t_start,1), ft, bt, fs_))
    time.sleep(0.3)

# 统计最大不一致持续时长：连续采样
out.append(f"SSE 断开: {[e for e in sse_errors] or '无'}")
out.append(f"前端/后端 turn 不一致瞬间: {stale_events[:10]}")
out.append(f"共 {len(stale_events)} 次不一致（其中 >2s 持续才算卡住）")
st = httpx.get(f"{BASE}/api/games/{game_id}/state", timeout=5).json()
out.append(f"最终: turn={st['turn']} stones={sum(1 for row in st['board'] for c in row if c!=0)} status={st['status']}")
httpx.delete(f"{BASE}/api/rooms/{room_id}", timeout=5)
# 输出文件路径与脚本同目录，用 with 确保正确关闭
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repro_out.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print('DONE')

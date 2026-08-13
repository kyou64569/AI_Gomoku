import os, sqlite3, random, time
from dotenv import load_dotenv
load_dotenv()
from app.services.llm_service import call_llm, build_prompt
from app.services.game_logic import create_board, place_stone

random.seed(7)
board = create_board()
history = []
for i in range(60):
    empties = [(r,c) for r in range(15) for c in range(15) if board[r][c]==0]
    r, c = random.choice(empties)
    board = place_stone(board, r, c, 1 if i%2==0 else 2)
    history.append({'player': 1 if i%2==0 else 2, 'player_name': 'x', 'row': r, 'col': c})
prompt = build_prompt(board, 2, history, 'AI')

db = sqlite3.connect('gomoku.db')
rows = db.execute('SELECT c.id, c.name, c.base_url, c.api_key, p.model_id, p.temperature FROM model_configs c JOIN ai_players p ON p.model_config_id=c.id').fetchall()
db.close()
seen = set(); ok = 0; total = 0
for cid, name, base_url, api_key, model_id, temp in rows:
    if model_id in seen: continue
    seen.add(model_id); total += 1
    if not api_key:
        api_key = os.environ.get(f'LLM_API_KEY_{cid}', os.environ.get('LLM_API_KEY',''))
    t0 = time.time()
    row, col, reason = call_llm(base_url, api_key, model_id, prompt, temp/100.0)
    dt = time.time() - t0
    status = 'OK ' if row is not None else 'FAIL'
    if row is not None: ok += 1
    print(f'[{status}] {name:9s} {model_id:26s} {dt:5.1f}s -> {str(reason)[:40]}')
print(f'=== {ok}/{total} 成功（60 手复杂局面） ===')

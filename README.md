# AI Gomoku · 弈境

一个支持多模型配置的 AI 五子棋 Web 应用。

## 功能
- 多模型配置管理（OpenAI 兼容接口）
- AI 玩家管理（采样温度固定 0.2，低随机稳定落子）
- 人机模式 / 观战模式
- 房间制对战（可退出重进）
- 专业禁手规则（黑棋长连/双三/双四禁手）+ 确定性评分引擎 + LLM 增强
- 精美响应式 UI（桌面 + 移动端），落子动画、音效、AI 思考日志

## 快速开始

> **前端即用**：前端由后端 FastAPI 直接托管 `frontend/index.html`，无需 Node 构建。

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000

### 密钥安全（重要）
- API Key **不落库**：创建模型配置时 `api_key` 留空即可，运行期从环境变量读取。
- 复制 `backend/.env.example` 为 `backend/.env`，按配置 ID 填写：
  ```ini
  LLM_API_KEY_2=sk-xxx   # 对应模型配置 id=2
  LLM_API_KEY=sk-xxx     # 兜底（所有配置）
  ```
- 若旧库中已有明文 Key：停止服务后运行 `python backend/migrate_keys_to_env.py` 一键迁移到 `.env` 并清空数据库字段。
- `.env`、`*.db` 均在 `.gitignore` 中，不会提交。

### 一键启动（Windows）
```bash
start.bat   # 自动探测 8000-8099 空闲端口并启动
stop.bat    # 停止服务
```

## 目录结构
```
backend/
  app/
    main.py          # FastAPI 入口（托管前端静态文件）
    database.py      # SQLAlchemy engine / session
    models/          # 数据模型（Base / ModelConfig / AIPlayer / Room / Game）
    routers/         # configs / players / rooms / games API
    services/        # 游戏逻辑、LLM 服务、AI 决策引擎、密钥解析
  tests/             # pytest 测试（16 个用例）
  migrate_keys_to_env.py  # 密钥迁移脚本（一次性）
  requirements.txt
  pyproject.toml

frontend/
  index.html         # 主前端（内联 JS + Tailwind CDN，由 FastAPI 托管）
  src-archived/      # 早期 React 实验版（未接入，仅供参考）
  package.json       # React 实验版依赖清单（已归档）

docs/                # 架构 / PRD / 代码审查 / UI 方案文档
```

## 测试
```bash
cd backend
pytest tests/ -q   # 16 passed
```

## AI 思考日志解读

AI 落子走的是「本地引擎优先 + LLM 增强」的双层决策（`backend/app/services/llm_service.py`）。落子后「AI 思考日志」面板里每一行都按下面表格解读：

| 场景 | 日志显示 |
|---|---|
| LLM 调用失败（超时/401/空内容） | `[AI] 引擎: 攻500/防500（LLM 调用失败: Connection error.）` |
| LLM 返回非法坐标 | `[AI] 引擎: 攻0/防500（LLM 返回非法坐标 (12,3)）` |
| LLM 落点是黑棋禁手 | `[AI] 引擎: ...（LLM 落点为禁手 (7,7)）` |
| LLM 点质量不达标 | `[AI] 引擎: ...（LLM 评分不足: 800 < 引擎 1200）` |
| LLM 调用抛异常 | `[AI] 引擎: ...（LLM 异常: TimeoutError: ...）` |
| 引擎有绝对把握（没问 LLM） | `[AI] 引擎: 一步连五 (7,7)（无后缀 = 正常快刀）` |
| LLM 被采纳 | `[AI] LLM: 形成活三，威胁对方` |

规则要点：

- **引擎快刀**（无后缀）：连五 / 堵活四等绝对把握时直接下，不问 LLM——这是设计行为，不是故障。
- **有「（LLM ...）」后缀**：意味着 LLM 调了但被拒/失败，引擎兜底。后缀内容即具体原因（超时 / 解析失败 / 禁手 / 评分不足）。
- **当前缀是 `LLM:`**：大模型给出的点通过了合法性、禁手、质量三重校验被采纳。
- **超时**：单次 LLM 请求 30 秒（`client.timeout=30.0`），加 response_format 降级与 JSON 解析重试，理论最坏 120 秒。

## License
MIT

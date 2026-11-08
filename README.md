# AI Gomoku · 弈境

一个支持多模型、多协议配置的 AI 五子棋 Web 应用。

## 功能
- 多模型配置管理：OpenAI 兼容 / Google Gemini / Anthropic 三种协议，API Key 可留空走环境变量
- 思考（Reasoning）参数统一适配：7 种 OpenAI 兼容方言 + Gemini/Anthropic 原生分支，玩家档位 默认/关闭/低/中/高
- AI 玩家管理、人机模式 / 观战模式、房间制对战（可退出重进）
- AI 擂台赛（双 AI 自动对弈产出棋力对比）与 AI 复盘点评
- 单局导出：对局/历史页一键下载 思考日志 + 落子记录 + 复盘（TXT / JSON）
- 运行指标页：调用成功率 / 延迟 / 参数自动降级计数
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
start.bat   # 自动探测 8000-8099 空闲端口并启动（优先使用 backend\.venv）
stop.bat    # 停止服务
```

### 桌面版 EXE（可选，不影响网页版）
```bash
package-exe.bat   # 一键打包（PyInstaller + pywebview）
```
产物 `dist\AI-Gomoku-Desktop\AI-Gomoku-Desktop.exe`，双击即用：

- 内嵌服务（127.0.0.1，自动探测 8000-8099，已有本应用实例直接复用）+ 原生窗口（WebView2，缺失时自动回退默认浏览器）；关闭窗口即退出。
- 与网页版共用同一套 FastAPI 应用与前端，功能完全一致。
- **数据与密钥自动共享**：exe 在仓库目录内运行时自动探测到 `backend/`，直接使用网页版同一份 `backend\gomoku.db` 与 `backend\.env`（两端看到的模型配置、对局数据完全一致，可同时运行，SQLite 忙等待保护并发）；整个文件夹拷出仓库单独分发时，才退回 exe 同级 `data\gomoku.db` 独立数据。排查日志：`data\desktop.log`。
- 自动化冒烟：`AI-Gomoku-Desktop.exe --selftest`（会输出读到的模型配置数）。
- 注意：未签名 exe 首次运行可能被 SmartScreen / 杀软提示，点「仍要运行」即可。

## 模型配置与思考参数

配置页的模型配置卡片上可直接切换以下三项（改动即保存）：

- **协议类型**：`openai`＝OpenAI 兼容 `/chat/completions`（base_url 通常以 `/v1` 结尾）；`gemini`＝官方 generateContent（base_url 填裸域名，不带 `/v1beta`）；`anthropic`＝官方 Messages API（base_url 填裸域名，不带 `/v1`）。切换协议会清空已拉取的模型列表，需重新拉取。
- **思考方言**（仅 OpenAI 兼容协议）：不同网关「开/关思考」的字段位置与形状不同（顶层 `enable_thinking` / 嵌套 `chat_template_kwargs` / 字符串·对象·布尔 `thinking` / `thinking_mode` / 仅 `reasoning_effort`），已收敛为 7 种数据驱动方言，选错可在卡片上就地修正。
- **思考能力声明**：`graded`（低/中/高各自生效）/ `binary`（只分开关，各档一律满血）/ `none`（不发任何思考参数）。
- **玩家思考档位**：默认（不发任何思考字段，跟随模型默认）/ 关闭 / 低 / 中 / 高。思考开启时自动抬高输出预算，防止思考 token 把落子 JSON 挤截断。
- **兼容性自愈**：思考参数或 `response_format` 被端点 4xx 拒绝时自动降级重试，成功组合按（端点 × 模型 × 档位 × 方言）缓存，后续调用直发成功组合；指标页的「参数降级」即被拒次数。

## 目录结构
```
backend/
  app/
    main.py          # FastAPI 入口（托管前端静态文件 + 启动时轻量列迁移）
    database.py      # SQLAlchemy engine / session
    models/          # 数据模型（ModelConfig / AIPlayer / Room / Game / Arena*）
    routers/         # configs / players / rooms / games / arena / stats API
    services/        # 游戏逻辑、LLM 服务、思考方言注册表（thinking_profiles）、
                     # 原生协议分支（native_protocols）、复盘 / 擂台 / 指标、密钥解析
  tests/             # pytest 测试（144 个用例）
  desktop_main.py    # 桌面版入口（内嵌 uvicorn + pywebview 窗口，与网页版共用 app/）
  migrate_keys_to_env.py  # 密钥迁移脚本（一次性）
  requirements.txt
  pyproject.toml

AI-Gomoku-Desktop.spec     # PyInstaller 打包配置（桌面版）
package-exe.bat            # 桌面版一键打包脚本
requirements-desktop.txt   # 桌面版专用依赖（pywebview / pyinstaller）

frontend/
  index.html         # 主前端（内联 CSS + 原生 JS，无构建，由 FastAPI 托管）
  src-archived/      # 早期 React 实验版（未接入，仅供参考）
  package.json       # React 实验版依赖清单（已归档）

docs/                # 内部文档（已被 .gitignore 忽略，仅本地保留）
```

## 测试
```bash
cd backend
python -m pytest tests/ -q   # 144 passed
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
- **超时**：单次落子调用总时长上限 45 秒（`LLM_CALL_DEADLINE`，已含参数自动降级与预算重试）。

## License
MIT

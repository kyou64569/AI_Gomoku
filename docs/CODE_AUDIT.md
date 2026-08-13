# 代码审计报告 — AI Gomoku 项目（GitHub 上传前）

> 审计日期：2026-08-11 ｜ 审计范围：`D:\work\AI_WuZiQi` 全部代码、配置、数据与文档
> 审计人：Code Review Expert ｜ 结论：**存在 🔴 高危密钥泄露风险，修复前禁止上传**

---

## 0. 审计摘要

| 维度 | 结论 |
|---|---|
| 🔴 敏感信息 | **1 处高危**：`backend/gomoku.db` 存有 4 组真实 API Key（明文） |
| 🟡 多余文件 | 缓存/编译产物/内部脚本若干，需清理 + `.gitignore`（已生成） |
| 🟡 代码质量 | 后端可运行、16/16 测试通过；**前端存在双实现结构问题**，React 版无法构建 |
| ✅ 源码硬编码 | 源码中未发现硬编码密钥（干净） |
| ✅ 私有 IP | 仅文档/测试中的 localhost/127.0.0.1，无真实内网地址 |

---

## 1. 🔴 敏感信息检查（最高优先级）

### 1.1 【🔴 Blocker】数据库明文存储真实 API Key

**位置**：`backend/gomoku.db` → `model_configs` 表（`api_key` 列）

**发现**：数据库中存在 **4 组真实、仍在使用**的 LLM API Key，全部明文存储：

| id | 配置名 | 密钥类型 | 关联玩家 |
|---|---|---|---|
| 2 | step | 明文 token（60 位） | 阶跃 |
| 3 | sensenova | `sk-` 前缀 | 商汤 |
| 4 | gemini | `AIzaSy` 前缀（Google） | 谷歌 3.1 lite |
| 5 | opencode | `sk-` 前缀 | 龙猫 |

**风险**：`gomoku.db` 位于后端目录内，一旦 push 到 GitHub，任何人均可下载数据库并提取这些 Key → 盗刷 API 额度、造成经济损失。Google Gemini Key 还可能关联 GCP 账单。

**处置建议（按顺序执行）**：
1. **立即**：`backend/gomoku.db` 加入 `.gitignore`（已加入），**绝不** `git add backend/gomoku.db`。
2. 若历史上已 push 过该文件：视为泄露，**立即去各平台控制台轮换这 4 个 Key**，并从 git 历史中清除（`git filter-repo` / BFG）。
3. **改用环境变量**：本项目 `requirements.txt` 已含 `python-dotenv`，但代码未使用。建议：
   - 新建 `backend/.env.example`（占位符，可提交）：
     ```ini
     # 各模型配置的 API Key，按需填写
     STEP_API_KEY=
     SENSENOVA_API_KEY=
     GEMINI_API_KEY=
     OPECODE_API_KEY=
     ```
   - `configs.py` 创建配置时改为「支持留空 + 回读环境变量」，或至少对 `api_key` 做掩码存储（存前缀+哈希），LLM 调用时从环境变量读取。
4. **数据库治理**：提交一个 `backend/schema.sql` 或迁移脚本（建表语句，无数据）代替 db 文件；首次运行自动建库（`Base.metadata.create_all` 已具备）。

### 1.2 【🟡】数据库路径硬编码

**位置**：`backend/app/database.py:3`、`backend/app/models/__init__.py:11`（`SQLALCHEMY_DATABASE_URL = "sqlite:///./gomoku.db"`）

**建议**：改用 `os.getenv("DATABASE_URL", "sqlite:///./gomoku.db")`，支持 `DATABASE_URL` 环境变量覆盖，便于部署与 CI 测试隔离。

### 1.3 【🟡】内部记忆/脚本含真实端点信息

**位置**：`.workbuddy/memory/2026-08-11.md:73` 记录了真实 LLM 端点域名（`api.stepfun.com` / `token.sensenova.cn`）；`.workbuddy/` 下还有 `probe_llm.py`、`e2e_*.py`、`diagnose_port.py` 等内部诊断脚本。

**建议**：`.workbuddy/` 整目录已加入 `.gitignore`。这些是开发期内部产物，不应公开。

### 1.4 ✅ 已确认安全

- 全部源码（`.py` / `.tsx` / `.html` / `.ts`）无硬编码密钥、无 token、无密码（grep 全项目零命中）。
- 无 `.env` / `.pem` / `.key` / `id_rsa*` / `credentials*` 等密钥文件。
- `localhost` / `127.0.0.1` 仅出现在文档与测试桩中，无真实内网/私有 IP。
- `.port` 仅 7 字节端口号，无敏感（已 gitignore）。

---

## 2. 🟡 多余文件与清理清单

### 2.1 应清理 / 排除的文件（已全部加入 `.gitignore`）

| 类别 | 具体路径 | 说明 |
|---|---|---|
| 运行时数据库 | `backend/gomoku.db`（69KB） | **含真实密钥**，绝不提交 |
| Python 编译缓存 | `backend/app/**/__pycache__/`（13 个 .pyc） | 自动生成 |
| pytest 缓存 | `backend/.pytest_cache/`（4 项） | 自动生成 |
| 运行时端口 | `.port` | start.bat 写入 |
| 内部工作区 | `.workbuddy/`（记忆日志、诊断/e2e 脚本） | 开发期内部产物 |
| IDE/OS 文件 | `.DS_Store`、`Thumbs.db`、`.idea/`、`.vscode/` 等 | 按需防护 |
| 依赖/构建 | `node_modules/`、`dist/`、`.venv/`、`*.log` | 通用防护 |

### 2.2 需人工决策的文件

| 文件 | 建议 | 理由 |
|---|---|---|
| `backend/test_sse.py` | 删除或移入 `tests/` | 一次性手动 SSE 冒烟脚本，与 pytest 套件不统一 |
| `docs/GOMOKU_CODE_REVIEW.md` | 可选提交 | 内部审查记录，含改进建议，可留作项目文档 |
| `docs/UI_REDESIGN.md` | 可选提交 | 同上，且是近期 UI 改造方案，建议保留 |
| `docs/ARCHITECTURE.md` | **建议保留** | 架构文档，上传后对协作者有价值（注：当前根目录 `find` 显示其在 docs/ 下） |
| `overview.md` / `PRD.md` | 建议保留 | 项目概览与需求文档 |

### 2.3 ✅ 已确认无

- 无 `node_modules/`、`dist/`、`build/`（前端从未构建过）
- 无日志文件、无 `.DS_Store`、无系统临时文件

---

## 3. 🟡 代码质量与完整性评估

### 3.1 【🟡 Blocker 级】前端双实现，React 版本不可构建

**现象**：项目同时存在两套前端：
- **实际运行版**：`frontend/index.html`（内联 JS + Tailwind CDN，76KB，由 FastAPI 静态托管）—— 这就是线上用的。
- **未集成版**：`frontend/src/`（React 18 + TS，完整的路由/页面/组件/store 结构，但**从未构建、未接入**）。

**证据**：
1. `index.html` 中**没有** `<div id="root">`，也不引用 `src/main.tsx`（grep 计数 0）→ Vite 构建后 React 无法挂载。
2. `src/App.tsx` 使用 `react-router-dom`，但 `package.json` 的 dependencies **缺少 `react-router-dom`** → `npm install && npm run build` 必然报模块缺失。
3. `README.md` 目录树提到 `frontend/src/utils/`，实际**不存在**。
4. README 指引 `npm run dev` 访问 5173（React 版），但真实部署走 8000（FastAPI + 内联版）→ **文档与事实不符**。

**建议（三选一，推荐 A）**：
- **A（推荐）**：明确主线为内联版 —— README 更新为「前端由 FastAPI 直接托管 `frontend/index.html`，无需 Node 构建」；将 `frontend/src/` 归档到 `frontend/src-archived/` 或删除，避免协作者误以为 React 版是主实现。
- B：若希望 React 版成为主实现 —— 需补 `react-router-dom` 依赖、在 `index.html` 加 `#root` 挂载点与 `<script type="module" src="/src/main.tsx">`、修复 vite proxy，并完成一次真实构建验证。
- C：维持现状但补齐说明 —— README 注明两套前端的关系与维护边界。

### 3.2 【🟡】`requirements.txt` 版本与实测不一致

**位置**：`backend/requirements.txt:5`（`sqlalchemy==2.0.30`）

**发现**：本机实际运行环境为 **sqlalchemy 2.0.51**；2.0.30 在 Python 3.13 下已知不兼容（项目早期踩坑并升级，但清单未同步）。按清单全新安装会失败或产生告警。

**建议**：更新为 `sqlalchemy==2.0.51`，并顺手同步 `pyproject.toml`。

### 3.3 【💭】SQLAlchemy 2.0 弃用警告

**位置**：`backend/app/models/__init__.py:4,10`（`from sqlalchemy.ext.declarative import declarative_base` + `declarative_base()`）

**建议**：改为 `from sqlalchemy.orm import DeclarativeBase`（2.0 标准写法），消除 `MovedIn20Warning`。

### 3.4 【💭】依赖清单冗余

`requirements.txt` 与 `pyproject.toml` 的 dependencies **重复声明且内容一致**。建议二选一为主（推荐保留 `pyproject.toml` + `uv pip install -e .` 或 `pip install -e .`），`requirements.txt` 可保留给无构建工具的用户，但注明同步维护。

### 3.5 ✅ 已验证通过

- **后端可运行**：`uvicorn app.main:app` 正常启动，`/`、`/docs`、`/openapi.json` 及 4 个业务 API 全部 200。
- **测试通过**：`pytest tests/` → **16 passed**（game_logic / engine_forbidden / llm_service 全绿）。
- **依赖清单存在**：`requirements.txt`、`pyproject.toml`、`package.json` 均在，缺失的 `react-router-dom` 是双实现问题而非遗漏。
- **构建运行**：内联前端由 FastAPI 托管即开即用；后端 `start.bat` / `stop.bat` 完备（端口 8000-8099 自适应）。

---

## 4. 修复行动清单（按优先级）

| # | 优先级 | 事项 | 位置 | 动作 |
|---|---|---|---|---|
| 1 | 🔴 | 数据库密钥泄露 | `backend/gomoku.db` | gitignore + 绝不提交；若已 push 立即轮换 4 个 Key |
| 2 | 🔴 | 前端双实现 | `frontend/src/` vs `index.html` | 按 §3.1 方案 A/B/C 决策并落实 |
| 3 | 🟡 | 密钥接入环境变量 | `backend/app/routers/configs.py` | 加 `.env` 支持（依赖已具备），Key 不落库 |
| 4 | 🟡 | 依赖版本同步 | `backend/requirements.txt` | `sqlalchemy==2.0.51` |
| 5 | 🟡 | 提交前清理 | 全项目 | 执行 `.gitignore`（已生成），删除 `test_sse.py` 或迁移 |
| 6 | 💭 | SQLAlchemy 2.0 化 | `models/__init__.py` | 换 `DeclarativeBase` |
| 7 | 💭 | README 对齐 | `README.md` | 修正前端启动说明与目录树 |

---

## 5. 上传前检查清单（Checklist）

- [ ] `backend/gomoku.db` 未被 `git add`（已被 .gitignore 挡住）
- [ ] 确认无历史提交包含 db 文件（`git log --all --oneline -- backend/gomoku.db`）
- [ ] `.env` / `.env.*` 不在提交列表
- [ ] `git status` 中无 `__pycache__` / `.pytest_cache` / `.workbuddy` / `.port`
- [ ] 若公开仓库：核对 README 无内部真实端点/密钥引用
- [ ] 本地先跑一遍 `pytest tests/` 全绿后再 push

> **最终结论**：后端代码质量良好（测试全绿、结构清晰、API 完备），但 **`gomoku.db` 中的真实 API Key 是上传前必须解决的高危项**；前端双实现与依赖版本问题建议同步处理，避免仓库内容引起协作者误解。修复以上 1-2 项后即可安全上传。

---

## 6. 修复执行记录（2026-08-11 19:15，已完成）

| # | 原问题 | 修复内容 | 验证 |
|---|---|---|---|
| 1 | 🔴 数据库明文密钥 | 新增 `services/config_helper.py`（`resolve_api_key`：db 值 → 环境变量 `LLM_API_KEY_{id}` → `LLM_API_KEY` 兜底）；`main.py` 加载 `load_dotenv()`；`configs.py` 创建配置 key 可留空、test/fetch 走 `resolve_api_key`；`game.py` AI 落子链路同样接入 | ✅ 配置2 测试「连通成功」；db 明文 key = 0 |
| 2 | 🔴 密钥迁移 | 新增 `backend/migrate_keys_to_env.py`，已将 4 组真实 key 迁移至 `backend/.env` 并清空 db 字段；`.env` 已被 `.gitignore` 排除 | ✅ 迁移脚本临时副本 + 真实库均验证 |
| 3 | 🔴 前端双实现 | `frontend/src/` 归档为 `frontend/src-archived/`（React 实验版，未接入）；README 明确内联版为主线、无需 Node 构建 | ✅ FastAPI 托管页面正常，API 全 200 |
| 4 | 🟡 依赖版本 | `requirements.txt` + `pyproject.toml`：`sqlalchemy==2.0.51`（与实测环境一致） | ✅ pytest 16/16 |
| 5 | 🟡 一次性脚本 | 删除 `backend/test_sse.py`；清理全部 `__pycache__` / `.pytest_cache` | ✅ |
| 6 | 💭 SQLAlchemy 2.0 | `models/__init__.py` 改用 `DeclarativeBase`，移除废弃 `declarative_base` 与冗余 engine/SessionLocal（由 `database.py` 统一提供） | ✅ 无 MovedIn20Warning，16/16 |
| 7 | 💭 README 对齐 | 重写 README：密钥安全指引、一键启动、真实目录结构 | ✅ |

**遗留说明**：
- pydantic 对 `model_id`/`model_config_id` 的 protected namespace 告警为无害 UserWarning（FastAPI Body 模型自动生成，无法通过 ConfigDict 消除），不影响运行。
- 迁移脚本为一次性工具，已保留在仓库供新环境使用。
- 若后续想启用 React 版：恢复 `src-archived` → `src`，补 `react-router-dom` 依赖、在 `index.html` 加 `#root` 挂载点后按 README 指引构建。

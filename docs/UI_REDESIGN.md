# 弈境 · AI 五子棋 — UI 改造方案

> 版本：v2.0 ｜ 日期：2026-08-11 ｜ 范围：`frontend/index.html`（后端 FastAPI 静态托管，单文件即前端全部）

## 1. 改造目标

在**不改动任何游戏逻辑与后端 API** 的前提下，将原版简陋页面重构为：

| 需求 | 达成方式 |
|---|---|
| 统一协调配色 | 暗色高级感配色系统：深邃墨蓝背景 + 胡桃木棋盘 + 琥珀柔和高光 |
| 棋盘材质与网格 | CSS 多层渐变模拟木质纹理 + 1px 柔和网格线 + 五处星位 |
| 棋子质感 | 三档径向渐变高光球体 + 内外阴影；下落回弹动画；最近一步标记；获胜连线脉冲 |
| 布局信息清晰 | 顶栏品牌、黑白双方玩家卡（含回合激活态与思考 spinner）、回合徽标、对局信息卡、AI 思考日志侧栏、结果浮层 |
| 交互反馈 | 悬停幽灵子预览、落子动画+涟漪、AI 思考动画、Toast 轻提示、自定义弹窗（替换 prompt/alert/confirm）、终局浮层、重新开始加载态 |
| 响应式 | 桌面双栏（棋盘+侧栏）↔ 平板单栏 ↔ 移动端纵向堆叠，字号/间距用 clamp() 自适应 |
| 可访问性 | WCAG：焦点可见、aria 标签、prefers-reduced-motion 关闭动效、触控目标 ≥ 触控友好 |

## 2. 设计系统（Design Tokens）

### 2.1 色彩
```css
--bg-0:#070b14;  --bg-1:#0c1220;  --bg-2:#121a2d;   /* 页面背景渐变 */
--surface:#131c30; --surface-2:#1a2540; --surface-3:#223052; /* 卡片层级 */
--text-1:#e9eef7; --text-2:#9aa7bd; --text-3:#5d6b84;       /* 文字三级 */
--amber:#e8b45a; --amber-2:#f6cf85;   /* 主高光（琥珀金） */
--cyan:#6fd3ff; --green:#3ddc97; --red:#ff6b6b;             /* 语义色 */
--board-line:rgba(228,190,138,.30);  --board-star:rgba(240,205,155,.55); /* 棋盘线/星位 */
```
- 主强调色为**琥珀金**，用于按钮、激活态、回合指示、胜利高亮，与深色底形成「柔和高光」对比。
- 语义色：进行中=青、成功=绿、危险=红，均带 13% 透明度底色，暗色下不刺眼。

### 2.2 棋盘材质（三层叠加）
```css
--board-wood: radial-gradient(130% 140% at 30% 0%, #473323, #382614 45%, #27180c 78%, #180d05); /* 胡桃木基色 */
--board-grain: repeating-linear-gradient(93deg, rgba(0,0,0,.055) 0 1px, transparent 1px 5px)
             + repeating-linear-gradient(3deg,  rgba(0,0,0,.03)  0 2px, transparent 2px 7px);  /* 木纹 */
网格线：linear-gradient 1px 细线，以 calc(100%/15) 平铺，覆盖于木纹之上 → 连续木纹 + 均匀网格。
```
棋盘外围再包一层深色木质边框（.board-frame），形成「棋盘嵌入木框」的立体层次。

### 2.3 棋子（球体质感）
- **黑子**：`radial-gradient(circle at 32% 26%, #a3b0c2, #4b5563 18%, #1f2937 55%, #05070d)` —— 高光偏左上，暗部收边。
- **白子**：`radial-gradient(circle at 32% 26%, #fff, #f1f5f9 32%, #cbd5e1 62%, #8b98ab)` —— 象牙白暖调。
- 统一 `box-shadow`：外投影（悬浮感）+ 内上高光 + 内下暗边（立体感）。

### 2.4 动效曲线
- 落子：`cubic-bezier(.34,1.4,.44,1)` 弹性回弹（下落 16px → 过冲 2px → 回弹 1px → 归位）。
- 常规过渡：`cubic-bezier(.22,1,.36,1)` 缓出。
- 涟漪环：落子时 `stone--new::after` 扩张消散；获胜连线 `winPulse` 呼吸光晕。

## 3. 页面结构

### 3.1 顶栏（Topbar，所有页面共用）
品牌 Logo（CSS 双石子）+ 名称「弈境·五子棋」；右侧按页面显示：配置管理 / 对局编号 chip / 返回。

### 3.2 首页 `renderHome()`
1. **Hero**：渐变文字标题 + 标语 + 黑白双子装饰。
2. **创建对局卡**：人机对弈 / AI 观战分段选择器 → 黑方/白方座位选择器（带子色 chip）→ 创建按钮 → 模式提示文案。
3. **对局列表**：房间卡片含状态徽章（等待开始=琥珀 / 对局中=青）、模式标签、双方玩家名、开始/进入/删除按钮；空态插画文案。

### 3.3 对局页 `renderGame()`（桌面双栏）
```
┌ 玩家卡(黑) │ 回合徽标(居中) │ 玩家卡(白) ┐   ← 激活方高亮琥珀描边；AI 思考显示 spinner
┌────────────── 棋盘(木质边框) ──────────────┐  ← 终局时叠加结果浮层
┌── 控制：重新开始 / 返回首页 ──┐
│ 侧栏：对局信息卡(模式/状态/回合/手数)      │
│       AI 思考日志（自动滚底）               │
```
- **回合指示**：徽标文案自适应（你的回合·请落子 / AI 思考中… / AI 对弈中 / 对局结束）；当前回合玩家卡琥珀高亮 + 呼吸圆点。
- **结果浮层**：毛玻璃背景 + 卡片弹入动画，显示胜方图标与「再来一局 / 返回首页」。

### 3.4 配置页 `renderConfig()`
保留全部原功能与元素 ID（`cfg-name` / `cfg-url` / `cfg-key` / `player-name` / `player-model` / `player-temp` / `temp-val` / `edit-player-*`），仅重构为卡片式表单：模型配置（含测试/拉取/删除、选中态高亮）+ AI 玩家（新增/编辑内联表单/删除）。

## 4. 交互反馈清单

| 场景 | 实现 |
|---|---|
| 悬停空位 | 半透明「幽灵子」预览（按当前执子方颜色） |
| 落子 | `stone--new` 弹性下落 + 涟漪环 + WebAudio 嗒声（沿用原音效逻辑） |
| 最近一步 | 棋子中心圆点标记（黑子上琥珀点 / 白子上墨点 + 光晕） |
| 获胜 | 五连棋子脉冲光晕 + 结果浮层 |
| AI 思考 | 玩家卡 spinner + 徽标「AI 思考中…」 |
| 重新开始 | 自定义确认弹窗 → 按钮 loading spinner → 跳转新对局 |
| 操作反馈 | Toast（成功/错误/信息，自动消失）；删除类操作红色确认弹窗 |
| 创建配置/玩家 | 弹窗表单替代原生 prompt（温度滑杆实时显示数值） |

## 5. 响应式策略

| 断点 | 布局 |
|---|---|
| ≥1024px | 棋盘主列 + 300px 固定侧栏（sticky） |
| <1024px | 单列：棋盘 → 控制 → 侧栏（日志限高 220px，非 sticky） |
| <640px | 玩家栏改纵向（回合徽标置顶）、座位/表单单列、房间卡纵向堆叠、弹窗按钮全宽 |

字号/内边距均使用 `clamp(min, vw, max)` 自适应；棋盘始终 `aspect-ratio: 1/1` 保证正方形。

## 6. 可访问性

- 所有可交互元素 `:focus-visible` 琥珀描边；按钮/选中框带 `aria-label`。
- `prefers-reduced-motion: reduce` 下关闭全部动画与过渡。
- 棋盘 `user-select:none`、`touch-action: manipulation` 消除移动端双击缩放延迟。
- 弹窗含 `role="dialog" aria-modal="true"`，Toast 容器 `aria-live="polite"`。

## 7. 功能零回归保证

- **逻辑零改动**：`api/loadConfigs/loadRooms/createConfig/testConfig/fetchModels/deleteConfig/createPlayer/deletePlayer/switchMode/createRoom/startRoom/enterRoom/deleteRoom/playStoneSound/loadGame/connectSSE/makeMove/getSeatName/submitConfig/submitPlayer/savePlayerEdit/render` 全部保留原实现语义。
- 新增纯展示辅助：`toast / showModal / promptFields / confirmDanger / showResultModal / restartGame / goHome / findWinLine / isStarCell / getSeatNameFromRoom`，不触碰后端。
- 「重新开始」复用后端 `POST /api/rooms/{id}/start`（创建同房间新对局）并跳转，SSE 由原路由自动重建。
- 冒烟验证（jsdom + 真实后端）：首页/棋盘 225 格/星位/最近一步/落子动画触发/终局浮层/获胜高亮/弹窗/危险弹窗共 27 项断言全部通过，页面无运行时错误；`GET /api/{configs,players,rooms}/` 与 `GET /api/games/1/state` 均 200。

## 8. 交付物

| 文件 | 说明 |
|---|---|
| `frontend/index.html` | 改造后的完整单页（视觉层全部重写，逻辑层保留） |
| 本文件 | 改造方案文档 |

**验证方式**：启动 `start.bat` 后访问 `http://localhost:8000/`，进入 `#game/1` 可直接查看观战对局的新界面。

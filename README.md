# Work Reporter

自动日报/周报生成工具 — 定时截屏 → 本地 VLM 分析 → Markdown 报告。

## 功能

- **自动截屏**：可配间隔（默认 10 分钟），多显示器，感知哈希去重；启动即自动开启（`screenshot.auto_start`），**全天不限时段**
- **VLM 手动模式**：支持截图后不自动分析，改为手动点击「⚡ 分析」批量处理
- **隐私保护**：三层过滤 — 应用黑名单 → 区域模糊 → **OCR 内容门禁**（隐私 / 成人 / R18 命中即整图删除，不送云端 LLM）
- **异步队列**：VLM 分析全异步执行，不阻塞截图流程
- **工作记忆**：增量积累上下文，每 N 个事件 LLM 总结，注入后续分析 prompt
- **智能报告**：每日 18:00 / 周五 18:30 生成 Markdown 报告，LLM 优先 + 模板兜底；零事件时定时任务跳过（`scheduler.skip_empty_reports`）
- **系统托盘**：右键菜单（截屏/暂停/自动/VLM 模式/日报/周报），气泡通知，图标状态切换
- **Web 仪表盘**：`localhost:8766` 浅色主题，双栏布局，时间线 + 热力图 + LLM 输出 + 日志 Tab + Markdown 报告渲染
- **GPU 监控**：后台检测 GPU 利用率，>50% 自动切换文本 LLM 到小模型（仅当配置了 `text_llm.gpu_fallback_model`）

## 快速开始

```bash
uv sync                                    # 安装依赖
export DEEPSEEK_API_KEY=sk-...             # Windows: setx DEEPSEEK_API_KEY "sk-..."
.venv\Scripts\python.exe main.py           # 启动（调试用，前台看日志）
```

浏览器打开 `http://localhost:8766`。

**LLM 后端**：默认走 DeepSeek `deepseek-flash`（OpenAI 兼容，支持图像输入，`https://api.deepseek.com/v1`）。
需要环境变量 `DEEPSEEK_API_KEY`；`config.yaml` 里写的是 `${DEEPSEEK_API_KEY}` 占位，由 `config_loader` 展开 ——
**不要把真实 key 写进 `config.yaml`，该文件被 git 跟踪且仓库公开**。

若想改回本地 Ollama：把 `vision_llm` / `text_llm` 的 `base_url` 换成 `http://localhost:11434/v1`、
`model` 换成实际模型名、`api_key` 换成 `ollama` 即可。

## 开机自启

已注册 Windows 计划任务 `WorkReporter`（登录时以 `pythonw.exe main.py` 启动，工作目录为仓库根 `%USERPROFILE%\work-reporter` 这类本机路径，
失败重启 3 次、无运行时长限制、单实例）：

```powershell
Start-ScheduledTask -TaskName 'WorkReporter'    # 立即启动
Get-ScheduledTaskInfo -TaskName 'WorkReporter'  # 看上次运行结果
schtasks /End /TN WorkReporter                  # 停掉
```

## 配置

`config.yaml` 主要选项：

```yaml
screenshot:
  hotkey: "ctrl+shift+alt+s"
  pause_hotkey: "ctrl+shift+o"
  auto_interval_minutes: 10
  auto_start: true   # 启动即自动开启自动截屏（false = 需手动点托盘/仪表盘）
  vlm_auto: true     # true=自动 VLM 分析，false=手动点「⚡ 分析」

vision_llm:
  provider: "openai"
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-flash"
  api_key: "${DEEPSEEK_API_KEY}"
  timeout: 180
  max_retries: 5

text_llm:
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-flash"
  api_key: "${DEEPSEEK_API_KEY}"
  gpu_fallback_model: ""   # 留空 = 不做 GPU 降级

web_dashboard:
  port: 8766              # 8765 被 research_board_app 占用
  auto_open: false        # 开机自启时不弹浏览器

scheduler:
  skip_empty_reports: true  # 当天/本周零事件时定时任务跳过，不写空模板报告
```

> 自动截屏除托盘/仪表盘切换外，`screenshot.auto_start: true` 时启动即开启。

## OCR 内容门禁（Layer 3）

截图在**送去云端 LLM 之前**先本地 OCR，命中敏感内容直接按 `on_hit` 处理（默认整图删除、不留事件），
保证隐私 / 成人 / R18 内容不会离开本机。

```yaml
privacy:
  content_detection:
    enabled: true
    engine: "windows_ocr"        # Windows 内置 OCR（需系统语言包）| tesseract（需装二进制）
    language: "zh-Hans-CN"
    fail_closed: false           # OCR 不可用时：false=告警后放行，true=一律拦截
    on_hit: "delete"             # 命中后整图删除
    keyword_file: "data/sensitive_keywords.txt"  # 完整词表放本地不跟踪文件（一行一词）
    sensitive_keywords: [...]    # 仓库内只保留通用示例，两者会合并去重
    patterns: {id_card, bank_card, api_key}   # 强隐私正则
```

要点：

- **中文归一化**：Windows OCR 会把中文按字切分插空格（"训练题" → "训 练 题"），
  所以匹配前统一去掉 CJK 之间的空格，否则中文关键词永远匹配不到（这是本仓库踩过的坑）
- **ASCII 关键词走词边界**，避免 `jav` 命中 `JavaScript`
- **OCR 在模糊前的原图上判定**（先模糊会降低识别率），通过门禁的图才做区域模糊再上云
- 门禁状态见启动日志 `Layer3 OCR 门禁就绪: ...` 与 `/api/status` 的 `ocr_ready` / `ocr_status`
- `email / phone_cn / ip_address / password_dots` 误伤率过高，默认不参与门禁，需要时自行加回
- 完整敏感词表在 `data/sensitive_keywords.txt`（`data/` 不进 git），仓库只留示例词，避免公开个人维护的过滤清单

## 工作日界（重要）

一天从 **08:00 算到次日 08:00**（`scheduler.workday_start`），凌晨 0–8 点归**前一天**：

- 热力图横轴 = 08:00 → 22:00 → 24:00（= 次日 0 点）→ 次日 07:00 共 24 根柱，最右几根是次日凌晨
- 事件列表、截图计数、日报/周报聚合都用同一窗口（`EventStore.workday_bounds`），不会出现"图里算前一天、日报里算当天"的分裂
- 日报 18:00 出一份**进度版**（覆盖到此刻），次日 08:30 自动用完整窗口**重写同一份**（`daily_report_finalize_time`），这样熬夜到凌晨的活动也会进前一天日报
- 仪表盘支持 `?date=YYYY-MM-DD` 直链；不指定日期时跟随"当前工作日"（凌晨打开自动显示前一天）

## 活动分类

VLM 将截图归类为 9 种固定标签：

颜色唯一来源于 CSS 变量 `--cat-*`（列表左侧色带 / 徽章 / 热力图共用），heatmap 在页面加载时读取一次。

| 标签 | 颜色变量 | 说明 |
|---|---|---|
| 开发创作 | `--cat-code` `#4A90D9` | 写**软件**代码、修 bug、写软件文档、PPT、示意图 |
| 阅读浏览 | `--cat-doc` `#2ecc71` | 网页、新闻资讯、日常查阅 |
| 沟通协作 | `--cat-comm` `#f39c12` | 聊天、视频通话、协同编辑 |
| 复盘分析 | `--cat-browse` `#9b59b6` | 审查日志、检查更新、跑脚本排错、报表 |
| 会议讨论 | `--cat-meeting` `#e74c3c` | Zoom / Teams / 腾讯会议 |
| 设计绘图 | `--cat-design` `#1abc9c` | Figma / PS / 白板 / 思维导图 |
| 研究学习 | `--cat-learn` `#7B68EE` | **一切科研**：读论文、写/改 LaTeX 论文、定理推导证明、画论文图、文献综述 |
| 社交沟通 | `--cat-comm` `#f39c12` | 社交聊天（与沟通协作同色） |
| 娱乐休闲 | `--cat-other` `#95a5a6` | 视频、游戏、音乐、购物 |
| 其他 | `--cat-misc` `#8899aa` | 系统设置、文件管理等 |

分类判据（写在 VLM 的 system prompt 里，按顺序判断）：娱乐优先 → 科研优先（用 Codex/终端改论文也算研究学习）
→ 本工具自身开发归开发创作/复盘分析。历史数据里的旧类别名（学习研究/创作构建/分析计算/阅读查阅/未分类）
会在解析时自动映射到新名。

## 仪表盘

- **左栏**：活动时间线（最新在上，无高度限制）+ 24 小时堆叠柱状热力图 + LLM 原始输出
- **右栏**：日报面板，点击「生成日报」即时渲染 Markdown
- 事件分类可手动调整（下拉菜单）
- 常用 API：`POST /api/capture` 手动截屏、`POST /api/report/daily?date=YYYY-MM-DD` 生成某天日报、`GET /api/events/today/llm` 今日 LLM 事件

## 项目结构

```
work-reporter/
├── main.py                 # 入口，模块编排，GPU 监控
├── config.yaml             # 用户配置
├── src/
│   ├── screenshot.py       # 截屏、去重、热键
│   ├── privacy_filter.py   # 三层隐私过滤
│   ├── vision_analyzer.py  # VLM 客户端、system prompt、图像切分、JSON/MD 校验
│   ├── work_memory.py      # 增量工作记忆
│   ├── event_store.py      # SQLite 存储（WAL，线程安全）
│   ├── vlm_queue.py        # VLM 异步任务队列（生产者/消费者）
│   ├── report_generator.py # 日报/周报生成（LLM + 模板兜底）
│   ├── scheduler.py        # APScheduler 定时任务
│   ├── tray_app.py         # 系统托盘菜单
│   ├── web_dashboard.py    # HTTP 服务 + 仪表盘 HTML/CSS/JS
│   └── config_loader.py    # 配置加载、校验、默认值合并
├── data/
│   ├── work_reporter.db    # SQLite
│   ├── work_reporter.log   # 运行日志
│   └── screenshots/        # 截图存档
└── reports/
    ├── daily/              # 日报 .md
    └── weekly/             # 周报 .md
```

## 技术栈

Python 3.13+ · uv · DeepSeek `deepseek-flash`（OpenAI 兼容 API，可换回 Ollama/vLLM）· mss · pynput · pystray · APScheduler · Pillow · imagehash · pytesseract · SQLite (WAL)

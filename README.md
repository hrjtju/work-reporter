# Work Reporter

自动日报/周报生成工具 — 定时截屏 → 本地 VLM 分析 → Markdown 报告。

## 功能

- **自动截屏**：可配间隔（默认 10 分钟），多显示器，感知哈希去重；启动即自动开启（`screenshot.auto_start`），**全天不限时段**
- **VLM 手动模式**：支持截图后不自动分析，改为手动点击「⚡ 分析」批量处理
- **隐私保护**：三层过滤 — 应用黑名单 → 区域模糊 → OCR 敏感内容检测；自动模式下触发隐私的截图直接删除不留痕迹
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

## 活动分类

VLM 将截图归类为 9 种固定标签：

| 标签 | 颜色 | 说明 |
|---|---|---|
| 创作构建 | `#2563eb` | 写文档、代码、PPT、画图 |
| 阅读查阅 | `#16a34a` | 网页、PDF、文档、查资料 |
| 沟通协作 | `#ea580c` | 聊天、视频通话、协同编辑 |
| 分析计算 | `#9333ea` | 报表、脚本、数据分析 |
| 会议讨论 | `#dc2626` | Zoom / Teams / 腾讯会议 |
| 设计绘图 | `#0891b2` | Figma / PS / 白板 / 思维导图 |
| 学习研究 | `#4f46e5` | 教程、论文、教科书、背单词 |
| 娱乐休闲 | `#db2777` | 视频、游戏、社交媒体 |
| 其他 | `#78716c` | 系统设置、终端操作等 |

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

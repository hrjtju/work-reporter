# Work Reporter

自动日报/周报生成工具 — 定时截屏 → 本地 VLM 分析 → Markdown 报告。

## 功能

- **自动截屏**：可配间隔（默认 2 分钟），多显示器，感知哈希去重
- **VLM 手动模式**：支持截图后不自动分析，改为手动点击「⚡ 分析」批量处理
- **隐私保护**：三层过滤 — 应用黑名单 → 区域模糊 → OCR 敏感内容检测；自动模式下触发隐私的截图直接删除不留痕迹
- **异步队列**：VLM 分析全异步执行，不阻塞截图流程
- **工作记忆**：增量积累上下文，每 N 个事件 LLM 总结，注入后续分析 prompt
- **智能报告**：每日 18:00 / 周五 18:30 生成 Markdown 报告，LLM 优先 + 模板兜底
- **系统托盘**：右键菜单（截屏/暂停/自动/VLM 模式/日报/周报），气泡通知，图标状态切换
- **Web 仪表盘**：`localhost:8765` 浅色主题，双栏布局，时间线 + 热力图 + LLM 输出 + 日志 Tab + Markdown 报告渲染
- **GPU 监控**：后台检测 GPU 利用率，>50% 自动切换文本 LLM 到小模型

## 快速开始

```bash
uv sync                        # 安装依赖
ollama pull gemma4:12b         # 拉取视觉模型
ollama pull gemma4:e2b         # 拉取小模型（GPU 高负载降级用）
uv run python main.py          # 启动
```

浏览器打开 `http://localhost:8765`。

## 配置

`config.yaml` 主要选项：

```yaml
screenshot:
  hotkey: "ctrl+shift+alt+s"
  pause_hotkey: "ctrl+shift+o"
  auto_interval_minutes: 2
  vlm_auto: true  # true=自动 VLM 分析，false=手动点「⚡ 分析」

vision_llm:
  base_url: "http://localhost:11434/v1"
  model: "gemma4:12b"

text_llm:
  base_url: "http://localhost:11434/v1"
  model: "gemma4:12b"

web_dashboard:
  port: 8765
```

> 自动截屏通过仪表盘按钮或托盘右键菜单开启/关闭，无快捷键。

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

Python 3.13+ · uv · Ollama (gemma4:12b / gemma4:e2b) · mss · pynput · pystray · APScheduler · Pillow · imagehash · pytesseract · SQLite (WAL)

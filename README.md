# Work Reporter

自动日报/周报生成工具 — 定时截屏 → 本地 VLM 分析 → Markdown 报告。

## 功能

- **自动截屏**：可配置间隔（默认 10 分钟），多显示器支持，感知哈希去重
- **隐私保护**：三层过滤 — 应用黑名单 → 区域模糊 → OCR 敏感内容检测
- **VLM 分析**：通过 Ollama 本地视觉模型分析截图内容，识别工作活动
- **工作记忆**：增量积累上下文，LLM 定期总结近期活动
- **智能报告**：每日 18:00 生成日报、周五 18:30 生成周报（Markdown）
- **系统托盘**：右键菜单快速操作，气泡通知
- **Web 仪表盘**：`localhost:8765` 实时查看事件、热力图、LLM 原始输出

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 确保 Ollama 运行并拉取模型
ollama pull gemma4:12b

# 3. 启动
uv run python main.py
```

启动后浏览器自动打开 `http://localhost:8765`。

## 配置

编辑 `config.yaml`（首次运行自动生成默认配置）：

```yaml
screenshot:
  hotkey: "ctrl+shift+alt+s"     # 手动截屏快捷键
  pause_hotkey: "ctrl+shift+o"   # 暂停快捷键
  auto_interval_minutes: 10      # 自动截屏间隔（分钟）

vision_llm:
  provider: "ollama"
  base_url: "http://localhost:11434/v1"
  model: "gemma4:12b"
```

> 自动截屏通过仪表盘按钮或托盘右键菜单开启/关闭（无快捷键）。

## 活动分类

VLM 将截图归类为以下 9 种固定标签：

| 标签 | 颜色 | 说明 |
|---|---|---|
| 🔵 创作构建 | `#4A90D9` | 写文档、写代码、做 PPT、画图 |
| 🟢 阅读查阅 | `#2ecc71` | 浏览网页、读 PDF、查资料 |
| 🟠 沟通协作 | `#f39c12` | 聊天、视频通话、协同编辑 |
| 🟣 分析计算 | `#9b59b6` | 报表、脚本、数据分析 |
| 🔴 会议讨论 | `#e74c3c` | Zoom/Teams/腾讯会议 |
| 🩵 设计绘图 | `#1abc9c` | Figma、PS、白板、思维导图 |
| 🔷 学习研究 | `#7B68EE` | 看教程、读论文、背单词 |
| 🩷 娱乐休闲 | `#95a5a6` | 视频、游戏、社交媒体 |
| 📌 其他 | `#8899aa` | 系统设置、终端操作等 |

## 项目结构

```
work-reporter/
├── main.py                 # 应用入口
├── config.yaml             # 用户配置
├── src/
│   ├── screenshot.py       # 截屏、去重、热键、隐私重试
│   ├── privacy_filter.py   # 三层隐私过滤
│   ├── vision_analyzer.py  # VLM API 客户端、图像切分
│   ├── work_memory.py      # 增量工作记忆
│   ├── event_store.py      # SQLite 事件存储
│   ├── report_generator.py # 日报/周报生成
│   ├── scheduler.py        # APScheduler 定时任务
│   ├── tray_app.py         # 系统托盘
│   ├── web_dashboard.py    # Web 仪表盘
│   └── config_loader.py    # 配置加载与校验
├── data/
│   ├── work_reporter.db    # SQLite 数据库
│   └── screenshots/        # 截图存档
└── reports/
    ├── daily/              # 日报 .md
    └── weekly/             # 周报 .md
```

## 技术栈

- Python 3.13+ · uv · Ollama (gemma4:12b)
- mss · pynput · pystray · APScheduler
- Pillow · imagehash · pytesseract
- SQLite (WAL mode)

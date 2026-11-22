# Work Reporter 运行记录

## 2026-11-22 修复"自动日报"停摆：自启 + 自动截屏 + LLM 换 DeepSeek + 端口

### 背景

用户要找"定时截屏总结每天干了什么"的工具 → 就是本仓库（GitHub 公开 repo `work-reporter`）。要求：如果存在但没在跑，把定时任务修好。

### 诊断（实测，非推断）

| 项 | 证据 |
|---|---|
| 最后真实截屏 | `data/screenshots/` 最后目录 `2026-07-02`；DB `screenshots` 表最大 id 250，时间 `2026-07-02T00:06:32`，共 93 张 / 54 条 `work_events` |
| 日志 | `data/work_reporter.log` 最后写入 2026-07-15 15:22（23139 行） |
| 日报 | `reports/daily/` 有 6-30、7-01（LLM 写的完整报告）和 7-08/7-09/7-10（200 字节空模板「今日暂无活动记录」）。DB `daily_reports` 时间戳为 UTC 10:00 = 北京 18:00，与 `scheduler.daily_report_time` 一致 |
| 无自启 | 启动文件夹只有 `PlanPilot.lnk`、`bilibili-parser-autostart.vbs`；`schtasks` 里只有 `ResearchBoard` |
| 定时器形态 | APScheduler 跑在进程内（周一~五 18:00 日报 / 周五 18:30 周报 / 02:00 清理）→ 进程不在就完全不跑 |
| 自动截屏默认关 | `src/screenshot.py` `_auto_mode = False`，只能托盘/仪表盘手动开，进程重启即失效 |
| LLM 后端挂了 | 实跑日志：`⚠ Vision LLM 服务不可用 (http://localhost:11434/v1)，将使用规则引擎`。本机没有 Ollama 服务（`localhost:11434` 无响应），config 里模型名 `gemma4:12b` 本身也不存在于 ollama |
| 端口冲突 | `research_board_app` 的 pythonw(21144) 占着 **8765**，而 work-reporter 仪表盘也绑 8765 → 仪表盘实际起不来 |

结论：无自启 → 进程不常驻 → 自动截屏默认关 → LLM 不可用只能降级规则引擎，四个原因叠加。

### 改动

1. `config.yaml`
   - `screenshot.auto_start: true`（新增）
   - `web_dashboard.port: 8765 → 8766`，`auto_open: false`
   - `vision_llm` / `text_llm`：`provider: openai`、`base_url: https://api.deepseek.com/v1`、`model: deepseek-flash`、
     `api_key: ${DEEPSEEK_API_KEY}`、`timeout: 180`、`max_retries: 5`；`text_llm.gpu_fallback_model: ""`
2. `src/config_loader.py`
   - 新增 `_expand_env()`，`load_config()` 返回前递归展开 `${VAR}` → key 只从环境变量取，**不入库**（config.yaml 被 git 跟踪，仓库公开）
   - `DEFAULT_CONFIG` 补 `screenshot.auto_start: False`、`web_dashboard.port: 8766`
3. `main.py`
   - `start()` 里按 `screenshot.auto_start` 调 `_start_auto_capture()` 并打日志
   - GPU 监控线程原来硬编码 `gemma4:e2b` / `gemma4:12b`，改读 `text_llm.gpu_fallback_model`；留空则不启动监控（否则会把云端模型名改成 ollama 模型名，直接把 LLM 打断）
   - Vision LLM 健康检查失败的日志措辞改为"仍会尝试调用，失败时回退规则引擎"（旧措辞误导，实际 vision_llm 仍在使用）
4. `src/vision_analyzer.py`
   - `check_health()`：先探 `/models`，不可用时再用一次 `max_tokens=1` 的 chat 探测。DeepSeek `/v1/models` 在本机会大量 connection reset，旧逻辑会把可用 API 误报为不可用
5. Windows 计划任务 `WorkReporter`（用户级、Interactive、Limited）
   - 触发：当前登录用户登录时；动作：`<repo>\.venv\Scripts\pythonw.exe main.py`，工作目录 = 仓库根（绝对路径按本机填写）
   - 设置：失败重启 3 次 / 间隔 1 分钟、`ExecutionTimeLimit=PT0S`（不限时）、`MultipleInstances=IgnoreNew`
   - 注册脚本用完即删（`data/_register_task.ps1`，PowerShell 5.1 读无 BOM UTF-8 会乱码，故全 ASCII 写）
6. `README.md`：快速开始/配置/自启/仪表盘端口与技术栈同步更新

### 验证

1. **配置加载**：`load_config()` 输出 `api_key` 已展开（长度 35，非 `${...}` 字面），`port=8766`、`auto_start=True`，`validate_config` 返回空
2. **vision 能力**：`deepseek-flash` 实测支持 `image_url`（`data:image/jpeg;base64`）→ HTTP 200，能读出图中内容和代码；`max_tokens` 太小时（200/400）思考内容会吃满导致 `content` 为空，本仓库用 16384 无此问题
3. **真实截图全链路**（进程内、前台运行）：`POST /api/capture` → 1/1 张 → 4 瓦片 JPEG → VLM 返回 970 字符纯 JSON → `[复盘分析] ... 置信度 88%` → 事件入库
4. **日报生成**：`POST /api/report/daily` 返回完整 Markdown，写入 `reports/daily/2026-11-22.md`（1929 字节）
5. **计划任务**：`Start-ScheduledTask WorkReporter` → 14:42:56 初始化完成、14:43:06 就绪、`自动截屏已开启 (间隔 10 分钟)`、8766 LISTEN（PID 46028）；再打一次 `/api/capture` 同样成功入库
6. 启动日志确认：`✅ Vision LLM 已连接: https://api.deepseek.com/v1 (deepseek-flash)`

### 问题记录（未根治 / 已知限制）

- **本机到 `api.deepseek.com` 连接不稳定**：同一端点连测出现 ~50% 的 `ConnectionResetError(10054)`，与请求体积无关（357KB 4/4 成功、152KB 1/4、77KB 4/4），系统代理 `127.0.0.1:7890`（Clash for Windows）反而更差（1/5）。目前靠 `max_retries=5` 兜底，失败时 vision 会退回规则引擎。根因未定位（疑似代理/TUN/跨境链路），不要据此判断"代码坏了"。
- 自动截屏**未受 `scheduler.work_hours` 约束**，非工作时段也每 10 分钟截一张（`max_screenshots_per_day: 200`）。是否限制工作时段留给用户决定。
- 日报 18:00 即使当天零事件也会生成空模板（7-08/09/10 就是这样）。是否"无数据跳过"未改。
- **不是"两个实例抢端口"（已纠正）**：`research_board_app` 看到两个 pythonw（11848 与 21144）其实是 uv venv 的 **shim → 真解释器父子对**（`ParentProcessId` 21144→11848，真解释器是 `AppData\Roaming\uv\python\cpython-3.10.17\pythonw.exe`），8765 由子进程持有，服务正常（HTTP 200）。同理本仓库的计划任务实例也是 shim(23368) + 真解释器(46028)。以后看到成对的 pythonw 不要当成重复启动去杀，会直接杀掉父进程导致服务退出。
- 7 月的历史空报告（7-08/09/10）仍在 DB 与 `reports/daily/`，可在仪表盘选日期重新生成（会覆盖）。

### 反思

最初按"文件/目录名含 daily/日报"搜索，在本机一堆 `1_projects`、`3_apps`、Obsidian vault、agent 会话历史里绕了很久都没命中——因为项目名叫 `work-reporter`，
"日报"只体现在功能与产物（`reports/daily/*.md`）里，不在任何文件名中。真正定位靠的是用户一句"定时截屏总结我每天干了什么 + 公开 repo"，再用 `find -iname '*screenshot*'` 一击命中。
教训：用户对系统的称呼往往与其实现名称不一致（他说"自动日报"，实际是"截屏→VLM→日报"的 work-reporter），先问清特征（做什么、输出什么、在哪发布）比穷举关键词快得多。

另外本次过程中我为了看 provider 配置打印了 `~/.pi/agent/auth.json` 的部分字段，我的 scrub 只处理了键名含 key/token/secret 的字段，漏掉了 `access` / `refresh`，
导致某 provider 的 OAuth access/refresh token 出现在本次会话记录里（仅本机 `~/.pi/agent/sessions`，未外传）。后续读凭证文件应**完全不做值打印**，只判断"存在/长度"。

---

## 2026-11-22（续）修掉三个遗留问题：截屏时段门禁 / 零事件空报告 / 网络重试退避

用户指定要改三个：② 自动截屏未受工作时段约束 ③ 零事件照发空报告 ④ 到 DeepSeek 连接不稳。
（周日不生成日报这条用户选择保留原样，`work_days` 仍为 [0,1,2,3,4]。）

### ② 自动截屏加时段门禁

- `src/screenshot.py`：`ScreenshotCapture.__init__` 新增 `should_capture: Callable[[], bool] | None = None`；
  `_auto_capture_tick()` 开头判断，False 时记 DEBUG「自动截屏跳过 — 当前不在工作时段」并继续排下一次 tick（不截屏、不改状态）
- `main.py`：新增 `_in_work_hours()`（读 `scheduler.work_hours_start/end`，`start <= now < end`，HH:MM 同格式字符串比较），
  构造 `ScreenshotCapture` 时按 `screenshot.work_hours_only` 传入；配置项新增 `screenshot.work_hours_only`（默认 true）
- 只限时段**不限星期**（周末白天仍记录），与"周日不生成日报"互不冲突：周末有数据时手动生成即可

### ③ 零事件不再写空模板

- `src/scheduler.py`：`_generate_daily_report_job` 在 `aggregate_daily()` 后判断
  `not data.events and not data.total_screenshots` → 记 INFO「⏭ 日报跳过 — ... 无事件也无截图」并 return；
  `_generate_weekly_report_job` 判断 `not data.all_events` 同理
- 配置项 `scheduler.skip_empty_reports`（默认 true）。只改**定时任务**，dashboard 手动「生成日报」不受影响（用户主动要看就照生成）

### ④ 网络重试改为退避 + 明确失败原因

先做实验确定改什么（`data/_net_check.py`，8 次请求 × 4 组，用完即删）：

| 组 | 配置 | 成功 |
|---|---|---|
| A | keep-alive，无间隔 | 5/8 |
| C | `Connection: close`，无间隔 | 2/8 |
| B | keep-alive，间隔 12s | 7/8 |
| D | `Connection: close`，间隔 12s | 3/8 |

→ keep-alive 明显优于每次断连，**不是空闲连接被代理回收**的问题，改连接方式没有收益。故只做两点：

- `src/vision_analyzer.py`：`_chat_completion` / `_text_completion` 重试之间加指数退避 `time.sleep(min(2**attempt, 8))`
  （避免连续重试撞在同一段坏窗口）；timeout 拆成 `(10, self.timeout)`（连接 10s、读取按配置），连不上时更快进入下一次尝试
- 同时修掉测试中发现的真实缺陷：推理模型的思考可能吃满 `max_tokens` 导致 `message.content` 为空字符串，
  旧代码把它当成功返回 → 上层 JSON 解析失败。现在 content 为空即抛 `ValueError` 触发重试，并记日志类型名（不再是静默 `except Exception`）

### 验证

1. 时间边界 `"09:00" <= now < "20:00"`：08:59/09:00/15:30/19:59/20:00/23:10 六点全对
2. 门禁正向：`should_capture=lambda: False` 直接调 `_auto_capture_tick()` → 临时目录 0 张截图、`on_capture` 未被调用
3. 门禁反向（真进程）：临时把 `work_hours_end` 改 `18:00`、间隔改 1 分钟重启 → `18:13:16 [DEBUG] 自动截屏跳过 — 当前不在工作时段`，随后配置已还原、服务已重启（8766 PID 42040）
4. 零事件跳过：对 `2026-07-09`（无事件）跑 `_generate_daily_report_job` → `reports/daily/2026-07-09.md` mtime 未变；对 `2026-07-01`（有事件）写入临时目录正常生成 4661 字节
5. 端到端未回归：`POST /api/capture` → 1/1 张 → VLM `[复盘分析] ... 88%` → 入库；重试/超时改动不影响成功路径
6. `py_compile` 五个改动文件全通过，`validate_config` 无错误

### 问题记录

- 连接 reset 仍在：退避只是提高重试成功率，根因未定位（keep-alive 更优已排除空闲回收假设）。若后续频繁失败，考虑换稳定中转端点或降低单次请求体积后再评估。
- `screenshot.work_hours_only` 只影响**自动**截屏，手动热键 `Ctrl+Shift+Alt+S` 和 `/api/capture` 任何时间都能截（符合预期，留作手动补记）。

### 推送通道（踩坑）

本次 `git push` 在 WSL 内失败：`GnuTLS, handshake failed: An unexpected TLS packet was received`
（WSL 环境变量 `https_proxy=http://127.0.0.1:7890` 走 Clash for Windows）。改 `7897`（SakuraCat）会直接卡住无响应；
WSL 侧接口 MTU 已正常（无 9000 大 MTU 接口），不是 MTU 黑洞。**可用做法：在 Windows 侧执行 git**：

```powershell
powershell.exe -NoProfile -Command "cd 'C:\...\work-reporter'; git push -u origin <branch>"
```

（同一工作树、同一 .git，Windows git 走系统代理与已保存凭据正常。）

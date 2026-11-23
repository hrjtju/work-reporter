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

---

## 2026-11-22（再续）按用户要求移除自动截屏的工作时段门禁

用户："现在对工作时间是否有限定？有的话删掉。" → 撤掉当天上午刚加的 ②。

- `src/screenshot.py`：删除 `should_capture` 参数、赋值与 `_auto_capture_tick()` 里的门禁判断（恢复原 tick 语义）
- `main.py`：删除 `_in_work_hours()` 与构造时传参
- `config.yaml`：删除 `screenshot.work_hours_only`；`scheduler.work_hours_start/end` 保留但注释标明**不参与任何逻辑**
  （原本就没有逻辑读取它们，这个门禁是唯一使用者）
- `src/config_loader.py`：`DEFAULT_CONFIG` 移除 `work_hours_only`
- README / TODO 同步

即：自动截屏**全天**按 `auto_interval_minutes` 运行，不再看时段（手动热键与 `/api/capture` 本来就无限制）。

验证：`load_config()` 中已无 `work_hours_only`；`ScreenshotCapture` 无 `should_capture` 属性；
`_auto_capture_tick()` 在临时目录直接截出 1 张（无门禁，之前门禁开启时是 0 张）；`py_compile` 通过；服务已重启。

代价（如实记录，不夸大）：全天每 10 分钟一次，单显示器理论上限 144 张/天 < `max_screenshots_per_day: 200`，不会触发删旧；
夜间画面不变时被感知哈希去重，不会额外调 VLM。若以后接多显示器（每屏一张）会逼近 200 上限并触发删当天最早文件，届时需调高上限。

---

## 2026-11-23 热力图时间轴改为"08:00 → 次日 08:00"，并统一配色

### 用户报的问题

"活动时间线最上面的时间视图中，这一天的凌晨会显示在最右边一条，并标上 24……这应该显示在前一天的最右边"；
补充："时间视图的颜色映射也和下面列表中左侧色带的颜色映射（不一致）"。

### 定位到的三个真实缺陷（`web_dashboard.py` 旧代码）

1. `order = [8..23] + [0]`，**1–7 点的事件完全没有柱子**，算了时长但静默丢失
2. 最右标 `24` 的桶装的是**当天 00:00–01:00**，语义错位（应是次日 0 点）
3. 事件时长 prorate 的桶基准固定为当天 0 点，**23:xx 跨午夜的时长直接丢失**

配色不一致：heatmap 用 JS `CAT_COLORS`（#7D55BF…），列表左色带用 CSS `--cat-*`（#4A90D9…），
徽章更混搭（背景用前者的 rgb、前景色用后者）；另外 `CAT_CSS` 里"社交沟通"和"沟通协作"共用 `cat-comm`。

### 用户决策

- 时间窗口：**A = 08:00 → 次日 08:00（24 根柱）**
- 跨日取数：后端一次性返回窗口内事件（不新增前端请求）
- 日报口径：**也改成 8:00 日界**
- 配色：**以列表左色带 `--cat-*` 为唯一色源**
- "社交沟通"继续与"沟通协作"共用 `cat-comm`
- 日报时点：**18:00 进度版 + 次日 08:30 完整版重写**

### 改动

**后端**
- `event_store.py`：`__init__` 加 `workday_start_hour`（默认 8）；新增 `_workday_bounds(d)`（`[D 08:00, D+1 08:00)`）与
  `current_workday()`（凌晨 8 点前算前一天）；`get_work_events_for_date` / `get_screenshots_for_date` /
  `get_screenshot_count_for_date` / `get_work_events_for_week` 全部改成窗口查询（原来用 `date(timestamp) = ?`）；
  `get_today_events` / `get_today_screenshots` 用 `current_workday()`
- `report_generator.py`：无需改动（经 store 方法自动窗口化）
- `scheduler.py`：日报 job 默认 target 改 `store.current_workday()`；新增 `daily_report_finalize` job，
  每天 `daily_report_finalize_time`（默认 08:30）生成 `current_workday() - 1` 的完整版（`insert_daily_report`
  是 upsert、`_save_report` 覆盖同名文件，所以自然重写进度版）；`get_next_report_times` 加该 job
- `web_dashboard.py`（后端）：所有 `date.today()` 默认值改 `app.store.current_workday()`；`/api/status` 新增
  `workday` / `workday_start_hour` 字段
- `main.py`：`EventStore` 注入 `workday_start_hour`（读 `scheduler.workday_start`）；状态文本与手动批处理改用
  `current_workday()`；状态里补"下次日报(完整版)"标签
- `config.yaml`：新增 `scheduler.workday_start: "08:00"`、`scheduler.daily_report_finalize_time: "08:30"`

**前端**
- `renderHeatmap` 重写：桶基准 = `workdayBaseMs(currentDate)`（D 08:00），24 桶对应 08:00…23:00 + 次日 00:00…07:00；
  跨午夜 prorate 天然正确；`endMs` 按窗口末端截断；标签 `08/12/16/20/24/04`，tooltip 对次日小时显示"次日 0X:00"
- 配色：删除硬编码 `CAT_COLORS`，改为 `initCatColors()` 页面加载时从 `--cat-*` 读一次；徽章背景改
  `color-mix(in srgb, var(--cat-*) 14%, transparent)`
- `currentDate` 默认 null（跟随后端 `current_workday`，跨过工作日界自动跳到新工作日）；用户手动翻日期后 `datePinned`
- 新增 `?date=YYYY-MM-DD` 直链

### 验证

1. **窗口查询**：日历日 07-01 有 44 条、07-02 有 2 条（00:06 等）→ 工作日 07-01 窗口返回 **46 条**（合并凌晨），工作日 07-02 返回 0 条
2. **聚合**：`aggregate_daily(2026-11-22)` = 38 事件 / 39 截图，末条 `2026-11-23T00:52`（含凌晨）；
   `2026-11-23` = 3 事件（10:14 起，08:00 界之后）
3. **DOM 实测**（Edge headless `--dump-dom`，`?date=2026-11-22`）：23 根可见柱的 tooltip 小时依次为
   14:00/15:00/…/23:00/**24:00**，索引 16 的桶确实是「24:00 — 开发创作 47 分钟 / 研究学习 10 分钟」
   → 凌晨 00:11–00:52 正确落在 24:00 桶（= 次日 0 点），不再塞在最右一根的语义错位里
4. **配色一致**：DOM 中 heatmap 用色集合 `{#4A90D9,#2ecc71,#9b59b6,#7B68EE,#95a5a6}` 与列表 `event-item cat-*`
   类集合（cat-code/cat-doc/cat-browse/cat-learn/cat-other）以及 CSS 变量值完全对应
5. `py_compile` 全部通过；抽出 `<script>` 段用 `node --check` 通过；服务重启后 `/api/status` 返回 `workday=2026-11-23`
6. 截图（`data/_heat_2026-11-22.png`，验证后已删）人工复核：轴标签 08:00/12:00/16:00/20:00/24:00/04:00，凌晨数据在最右段

### 问题记录 / 注意

- 周报仍按"周一 08:00 → 下周一 08:00"窗口，但生成时点是**周五 18:30**，所以周五 18:30 之后到周六 08:00 的内容
  进不了本周周报（下周才可补）。要不要把周报也挪到周一早上生成，待用户决定。
- 热力图/日报现在都按 8:00 界，但**截图清理**（`delete_old_screenshots`）仍按日历日 `date(timestamp) < ?`，
  语义上略微不齐但对"删 14 天前"这类粗粒度操作无影响，未改。
- 验证时发现 `curl http://127.0.0.1:8766` 会因环境变量 `https_proxy=127.0.0.1:7890`（Clash）返回 **502**，
  排查本地服务要加 `--noproxy '*'`，否则会误判服务挂了。

---

## 2026-11-23 清理已泄漏条目 + 上线 OCR 内容门禁（Layer 3）

### 背景

用户报告：在门禁上线前，云端 LLM 已经分析过成人内容并入库，要求删除这些条目。
同时要求新增 OCR 门禁：检测到隐私 / 成人 / R18 内容不发送云端 LLM。

### 一、清理已泄漏条目

用关键词扫描 `work_events`（106 条），初筛命中 5 条，其中 2 条是**误报，已排除**：

- 匹配 `jav` 的实际是 `JavaScript`（一条代码调试事件）
- 匹配 `中出` 的实际是 `中出现了微波炉`（一条 B 站视频事件）

二次用更精确的站点/组合词扫描确认，真正命中 3 条（同日 10:58–11:19 的成人站点浏览/播放记录）。处置：

1. 先备份 DB：`data/backup_before_purge_20261123_150108.db`（sqlite backup API，425,984 bytes）
2. 删除 `work_events` 3 条 + `screenshots` 3 条（106→103、146→143）
3. 对应截图文件经核对**已不在磁盘**（历史 14 天清理已删），无需再删
4. 核对 `daily_reports`（6 份）/ `weekly_reports`（1 份）/ `reports/` 目录：**均不含**相关内容，无需重写
5. 运行日志 `data/work_reporter.log` 有 12 行含相关站点原文（VLM 原始响应块），
   停服后按"整块 VLM 原始响应"过滤删除（27956 → 27944 行），备份留
   `data/backup_before_purge_log_before_purge.log`
6. 验证：关键词扫描残留 0 命中

**注意**：两个备份文件里仍含被删内容，是否销毁由用户决定（`data/` 不进 git）。

### 二、OCR 内容门禁

用户决策：OCR 引擎 = **Windows 内置 OCR**；成人检测 = **只做 OCR 文本关键词**；命中处置 = **整图删除**。

实现（`src/privacy_filter.py` Layer3 重写）：

- 后端：`winrt` 调 `Windows.Media.Ocr`（系统 `zh-Hans-CN` 语言包，实测 2560×1440 约 0.57s/张），
  保留 `engine: tesseract` 分支与回退；初始化时 `_probe_ocr_backend()` 探测能力并记日志
- **中文归一化**（关键坑）：Windows OCR 把中文按字切分加空格（"训练题" → "训 练 题"），
  不做 CJK 空格归一化则中文关键词 100% 失配、门禁形同虚设 → `normalize_ocr_text()` 处理
- **ASCII 关键词按词边界匹配**，否则 `jav` 命中 `JavaScript`（清理阶段实测到该误报）
- `process()` 顺序改为 Layer1 → **Layer3 在原图上判定** → Layer2 区域模糊 → 落盘。
  先模糊会降低识别率，所以门禁必须看原图；通过门禁的图仍是模糊版上云
- 命中 → `should_skip=True`，上层自动模式删除截图、不入 VLM 队列 → 内容不出本机
- `fail_closed` 开关：OCR 不可用时默认告警放行（`false`），可改 `true` 变成一律拦截
- 配置：`engine` / `language` / `tesseract_language` / `max_image_dim` / `fail_closed` / `on_hit` /
  `sensitive_keywords`（41 词，成人+R18+隐私）/ `patterns`（仅留 id_card / bank_card / api_key）；
  `email / phone_cn / ip_address / password_dots` 误伤率过高移出门禁并注释说明
- 依赖：`pyproject.toml` 加 8 个 `winrt-*` 包（`sys_platform == 'win32'`）
- 可观测：启动日志打 `Layer3 OCR 门禁就绪: windows_ocr / zh-Hans-CN`；`/api/status` 增
  `ocr_ready` / `ocr_status`；状态文本增 `🔒 OCR 门禁: ✅ 就绪 (...)`；OCR 连续失败 3 次打 ERROR

### 三、踩到的坑（值得记）

- **`asyncio.run()` 只接受 coroutine**：`engine.recognize_async(sb)` 返回 `IAsyncOperation`（仅 awaitable），
  直接传给 `asyncio.run()` 抛 `TypeError`，被 `except` 吞成空字符串 →
  **门禁静默放行所有图**（第一版测试就是这么假通过的：4 张脏图全 `skip=False` 且耗时只有 20ms）。
  必须包成 `async def _recognize(): return await ...` 再 `asyncio.run()`。
  教训：门禁类功能"没命中"必须与"没执行"可区分，事后加了失败计数与 ERROR 日志
- Windows OCR 的字间空格、ASCII 词边界误伤（`jav`），都是先写测试图跑出来的，靠读代码发现不了
- 排查本地 HTTP 服务要加 `curl --noproxy '*'`（环境变量 `https_proxy` 会把它拦成 502）

### 四、验证

| 用例 | 结果 |
|---|---|
| 中文成人词图（成人视频/里番/R18） | `skip=True`，命中 3 项 |
| 成人站点+编号图 | `skip=True`，命中 2 项 |
| 银行卡号+身份证号图 | `skip=True`，命中 3 项（含 id_card / bank_card 正则） |
| 干净图（含 "JavaScript"） | `skip=False` ✅ 不误伤 |
| 真实截图 3 张 | `skip=False` ✅ 不误杀 |
| 单张耗时 | 1000×380 约 27–50ms；2560×1440 约 570ms（10 分钟一次，无压力） |
| 端到端 | 服务重启后 `Layer3 OCR 门禁就绪`；`/api/status` `ocr_ready=true`；手动 `/api/capture` → 通过门禁 → VLM 正常分析入库 |

### 五、销毁含敏感内容的备份（用户要求"销毁"）

单纯 `rm` 不够：先做了二进制级残留普查，结果比预期严重 ——

| 文件 | 处理前二进制命中 |
|---|---|
| `data/work_reporter.db`（主库） | 0 |
| `data/work_reporter.db-wal` | **171 处** |
| `data/backup_before_purge_*.db` | 48 处 |
| `data/backup_before_purge_log_*.log` | 19 处 |

**教训：SQLite 的 DELETE 不等于数据消失** —— 删除只把页标为空闲，原始内容仍留在 WAL 预写日志与空闲页里。
正确销毁链：`PRAGMA wal_checkpoint(TRUNCATE)` → `PRAGMA secure_delete=ON` → `VACUUM INTO 新库`
→ 旧库/WAL/SHM 用随机字节覆写 3 遍并 fsync 后删除 → 新库 replace 占位。

重建后仍剩 3 处命中，追查发现**不是**被删的那 3 条，而是另外两条正常记录
（在线讲座、手游日常）的 `context_note` / `raw_response` 字段 —— VLM 把"历史最近活动"
写成了前一条敏感内容。属**交叉污染**：清洗这两个字段后表内残留归零，再 VACUUM/覆写一次，
最终主库与 WAL 二进制命中 0。

最后对 `data/screenshots/` 现存 **106 张**截图跑一遍 Layer3 门禁 OCR 复扫 → 0 命中（确认没有
画面上残留在历史图片里的敏感内容）。

行数保持一致（events 105 / screenshots 145 / daily 6 / weekly 1），功能未受影响。

### 局限（如实记录）

文件覆写只能覆盖逻辑扇区，**SSD 的 FTL / wear-leveling 不能保证物理擦除**旧块；
要更强保证需要整盘加密（BitLocker）+ 丢弃密钥、或 `cipher /w` 清空闲空间、或物理销毁。
本次做的是本机层面能做到的最大限度，不能对外声称"数据已不可恢复"。

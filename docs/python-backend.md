# Python 后端

采集器直接使用 RQData SDK，Python 计算历史资金和分钟篮子，FastAPI 提供现有 ECharts 页面与只读 API。采集器和网页独立运行，网页按文件版本加载已发布的快照。更新历史数据无需构建网页或重启服务。采集失败时保留原快照。

## 本地启动

在项目根目录建立 Python 虚拟环境，执行 `python -m pip install -r requirements-python.txt`。Windows 可将该环境的解释器路径设为 `ETF_BACKEND_PYTHON`。采集前按[首次安装](operations.md#2-首次安装一次性)配置 RQData 凭据和数据路径。逐只 ETF 雷达的采集器独立取前 60 个交易日的参考数据，网页历史分钟样本由 `minute_days` 单独配置。

```powershell
$env:DATA_MODE = 'live'
$env:ENABLE_RADAR = '1'
# 采集历史，输出到 .env 配置的历史快照路径
& .\scripts\python-backend.ps1 history
# 网页默认在 8080，可由 PORT 或 .env 覆盖
$env:PORT = '8080'
& .\scripts\python-backend.ps1 web
```

在另一个终端启动 `& .\scripts\python-backend.ps1 -Mode live -Arguments @('--watch')`。Python 盘中采集使用 RQData `LiveMarketDataClient` 订阅分钟线和 tick，一个进程向现有看板和逐只 ETF 事件雷达发布结果。浏览器优先通过 `/api/live/stream` 接收约每秒合并的 Tick 更新，连接失败时每 2 秒读取 `/api/live` 缓存；日频检查仍独立进行。最新价、成交额、盘口和逐只时间随 Tick 更新，分钟图与放量确认仅使用完整 bar；首个完整分钟之前也可展示已经收到的报价。详见 [Tick 链路与验收](tick-live-20261119.md)。开市前启动可预载基准；**11:30–13:00 保持同一订阅不断流**。开市中启动、进程重启或上游断流时，用 RQData 分钟线补齐已结束分钟。

Linux 或已配置 SDK 凭据的 Python 环境可直接运行以下命令。环境变量只在相应组件的服务配置中设置，网页应使用只读的快照路径。

```sh
python -m etf_backend.collect history --config .local/rqdata-market.json
python -m etf_backend.collect live --watch
DATA_MODE=live ENABLE_RADAR=1 uvicorn etf_backend.web:app --host 127.0.0.1 --port 8080
```

回放用 `live --replay YYYY-MM-DD --cache <独立文件>` 生成缓存。网页同时配置该文件为 `FTSHARE_LIVE_CACHE_FILE`，并设 `RQDATA_ALLOW_REPLAY=1`。盘中模式应使用自己的缓存文件。

Windows 可直接用 `& .\scripts\python-backend.ps1 web -ReplayCache '.local/python-rqdata-replay.json'` 打开已生成的回放。普通 `web` 模式下，分时图、实时排行和成交雷达要等盘中采集器写入首份缓存。日度资金和历史图表读取历史快照。

## 本地缓存、增量更新与数据目录

历史快照、盘中缓存、拥挤度和放量归档默认写在 `data/private/`，该目录已被 `.gitignore` 忽略。可以把下面四个变量指向任意目录（支持绝对路径），例如统一放到 `D:\ftshare-data\`：

| 变量 | 默认 | 内容 |
|---|---|---|
| `FTSHARE_SEED_FILE` | `data/private/rqdata-seed.json` | 历史快照（只读网页读取） |
| `FTSHARE_LIVE_CACHE_FILE` | `data/private/rqdata-live.json` | 盘中分钟缓存（每日覆盖） |
| `CROWDING_FILE` | `data/private/rqdata-crowding.json` | 市场拥挤度日度快照（行业/公募/融资为同目录派生文件） |
| `ETF_BURST_DIR` | `data/private/etf-burst` | 逐 ETF 放量实时与历史归档 |

数据目录不要提交到 git。若必须放在仓库内，请沿用被忽略的 `data/private/`，或把新目录加入 `.gitignore`。

每日收盘后只更新最近窗口，不要重跑全量：

```powershell
& .\scripts\python-backend.ps1 history -Arguments @('--recent')
```

`--recent` 只重新查询最近 14 天重叠窗口（含上游修订），再与现有快照按日期合并：旧日期原样保留，窗口内用新值替换。成员、公式、分钟轴不一致，或合并会丢失旧的有效交易日时，命令直接失败且不覆盖文件。首次仍需一次全量 `history`。

拥挤度本身按模块增量更新：日度重查最近 5 个已归档交易日，行业/公募/融资各自维护独立文件。

主动成交专题对照读取 `data/private/etf-flow-comparison.json`，可独立导入和热加载。快照、Tick 输入与 SQLite 日志的容量参考见[运维手册](operations.md#42-容量)。

### 收盘补全

`live --close` 是收盘后的一次性补全：用当日 1m 线重建完整盘中快照（不带 replay 标记），用于修复采集器晚启动或断线造成的 14:51–15:00 缺口。

```powershell
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--close')
```

期望输出包含 `"through": "15:00"`。它只改盘中缓存，不动日频历史。

### 定时任务

以下为目标部署机的建议时间表，需自行配置操作系统或外部调度器；代码合并和普通启动不创建任务，亦不代表本机已配置。详见 [运维手册](operations.md#26-定时任务)：

| 任务 | 触发（Asia/Shanghai） | 动作 |
|---|---|---|
| `ETF 日频与拥挤度 09:20` | 周一至周五 09:20 | `scripts/update-daily-all.ps1` |
| `ETF 开盘前启动 08:25` | 周一至周五 08:25 | 检查 8080，必要时后台启动网页/采集器 |
| `ETF 日频补采 12:30` | 周一至周五 12:30 | `scripts/update-sources.ps1 -SkipCrowding`，补晚披露 |
| `ETF 周六公募拥挤度` | 周六 09:00 | `crowding --crowding-module=funds` |
| `ETF 收盘补全 15:10` | 周一至周五 15:10 | `live --close`，补全到 15:00 |

采集器为常驻进程：09:15 自动切换新交易日，15:02 后空闲；每天写 `data/private/collector.heartbeat`，`start-collector.cmd` 依据心跳避免重复启动。日常状态用 `status.cmd` 查看。

## 回放入口

以下命令生成指定日期的分时面板缓存。

```powershell
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--replay=2026-11-13','--cache=.local/python-rqdata-replay.json')
```

逐只 ETF 放量也支持独立回放。指定一个交易日或通过 `--start` 和 `--end` 补多个日期。启动网页后，在 ETF 异动雷达选择日期和回放时点。普通分时回放与放量事件回放可同时使用。监控标的默认与日度核心篮子一致（同一份 20 只），可在 `config/etf-burst.json` 的 `symbols` 覆盖；分位参数为 `amount_window_quantile`，按成交额计算。

```powershell
& .\scripts\python-backend.ps1 -Mode burst-replay -Arguments @('--start=2026-11-12','--end=2026-11-13')
```

Linux 对应命令为 `python -m etf_backend.collect burst-replay --end=2026-11-13`。已有 RQData 成交额导出时，可以用 `--burst-input` 离线回放。输入包含 `schema_version:2`、`metric:"turnover"`、`unit:"CNY"`，以及 `1m` 和 `1d` 两组 `[symbol, date, slot, amount]`。`amount` 为元，分钟与日线须同口径。采集和网页共用 `ETF_BURST_DIR`。默认参数在 `config/etf-burst.json`，自定义文件通过 `ETF_BURST_CONFIG` 或 `--burst-config` 指定。详细口径见[盘中放量研究](etf-intraday-burst-research.md)。

需要单日 ETF 和期货 Tick 同窗、日末申赎与 SQLite 日志时使用 `day-replay`。Windows 包装器、跨平台 Python 命令和离线输入见 [Tick 数据回放](tick-driver.md#tick-数据回放)。当前联网 Tick 使用 RQData，DDB 支持日频构建。

日度主动成交对照面板从独立专题 CSV 导入。筛选与操作见[日度对照操作](operations.md#35-日度主动成交与申赎对照)。

## API 与部署

盘中异动还支持 Tick 本地聚合、分钟内候选和供应商 bar 并行验证。默认 `config/etf-burst.json` 的 `minute_driver="provider"` 保留原正式确认来源，独立验证后可配置为 `tick`。新增 Tick 导出回放及归档隔离方式见 [Tick 驱动异动监控](tick-driver.md)。

现有网页使用 `/api/health`、`/api/board`、`/api/history`、`/api/live` 和 `/api/cumulative`，另含 `/api/crowding*`、`/api/etf-burst*` 和 `/api/etf-flow-comparison`。雷达历史使用 `/api/etf-burst/dates`、`/api/etf-burst?date=YYYY-MM-DD` 和日志接口。实时事件 SSE `/api/etf-burst/stream` 与行情 SSE `/api/live/stream` 应关闭反向代理缓冲。API 与采集器运行在 Python 环境，浏览器负责交互绘图。部署与日常运维见[运维手册](operations.md)。

部署目标可以是另一台 Windows 电脑、Windows/Linux 服务器，或腾讯云 CVM。腾讯云 CVM 可选 Windows Server 或 Linux，采集调度方式随操作系统选择。单机部署可把采集器和 Uvicorn 作为不同进程运行，并通过同一数据目录共享快照。容器多实例部署需要共享存储，并应把采集任务部署到独立的常驻或定时运行环境。 [腾讯云 CVM 镜像列表](https://cloud.tencent.com/document/product/213/93093)

小程序可以复用同一套 Python 后端和只读 API，通过 HTTPS 域名访问，后台服务器可运行 Windows 或 Linux。正式上线时，让 HTTPS 反向代理对外提供授权范围内的 API，Uvicorn 继续监听本机地址。在微信公众平台配置 `request` 合法域名，腾讯云中国大陆部署同步确认备案要求。移动端按首屏、分时与历史范围拆分响应，发布前确认 RQData 与未来 Wind 数据的对外展示授权。 [小程序网络要求](https://intl.cloud.tencent.com/zh/document/product/1219/61745) · [腾讯云备案说明](https://cloud.tencent.com/document/api/243/37402)

## 市场拥挤度观测台

Python 网页服务在 `/crowding` 提供独立观测台，读取 `/api/crowding?days=250&table_days=5`。查询范围按交易日计，实际日期以已归档数据为准。历史分位使用截至各日近五年内的有效样本，市值比值均值和标准差使用当前图表区间。

首次采集默认回溯约一年。后续执行会补齐新日期并重查最近五个已归档交易日，单独写入 `data/private/rqdata-crowding.json`。可用 `CROWDING_FILE` 指定采集和服务共用的路径。需要指定回补起止时，Windows 用显式 `-Arguments` 数组传递参数。

```powershell
& .\scripts\python-backend.ps1 crowding
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=industry')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=funds')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=financing')
```

Linux 使用下表对应命令。可加 `--start=YYYY-MM-DD` 指定回补起点。附加模块首次回溯约一年，公募持仓按季度发布。采集器只发布北京时间当天之前的已结束交易日。尚无快照时对应 API 返回 503，补数完成后刷新页面即可查看。

### 后续指标与计划任务

`/api/crowding/industry`、`/api/crowding/funds`、`/api/crowding/financing` 各自读取独立快照。采集异常时保留上次归档。可用 `--crowding-module=industry|funds|financing` 单独补数。

- 指数行业按交易日日度成分、市值和成交额汇总，RQData 申万一级分类在每个 ISO 周首次计算时缓存复用。周内行业变更可能存在延迟；固定申万 2014 版待 Wind API 核验，当前展示每期第一行业及峰值。
- 公募按 RQData 聚源 1101、1201 分类，合并有效多份额关系，取每只基金首次披露的前十大股中的 A 股。无法归类的持仓市值计入「未分类」。报告期用于横轴，公告日及基金覆盖独立披露。
- 融资使用沪深两市融资余额，市值为沪深 A 股本身的市值。名义 GDP 与 M2 按公布日期对齐。住户存款预留 Wind API 数据位，接入后应使用人民币余额及发布日期。

以下计划由后续部署机器的调度器配置。本机开发与预览时按需手动采集。

| 北京时间 | 模块 | 在目标机器上执行 |
| --- | --- | --- |
| 每天 07:40 | 成交额集中度与大小盘风格 | `python -m etf_backend.collect crowding --crowding-module=daily` |
| 每天 08:30 | 融资与市场估值 | `python -m etf_backend.collect crowding --crowding-module=financing` |
| 每周六 09:15 | 指数行业拥挤度 | `python -m etf_backend.collect crowding --crowding-module=industry` |
| 每周六 10:15 | 公募基金重仓集中度 | `python -m etf_backend.collect crowding --crowding-module=funds` |

**Windows 或 Windows Server** 使用任务计划程序，在目标机为每个模块建立一个任务。操作的「程序或脚本」填写目标机虚拟环境的 `python.exe` 绝对路径，「添加参数」填写表中 `-m etf_backend.collect …` 部分，「起始于」填写项目根目录。按目标机时区配置触发器，使执行时间对应表中的北京时间。用实际执行任务的账户配置 SDK 凭据，需在退出登录后运行时，选择「不管用户是否登录都要运行」，再用该账户验证取数与写快照。 [Windows 任务执行账户](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal?view=windowsserver2025-ps)

**Linux** 可为四个模块各配置一个 systemd oneshot service 和对应 timer。service 的 `WorkingDirectory` 设为项目根目录，`ExecStart` 使用目标机 Python 虚拟环境的绝对路径及表中参数。timer 的 `OnCalendar` 显式指定 `Asia/Shanghai`，例如 `*-*-* 07:40:00 Asia/Shanghai` 或 `Sat *-*-* 09:15:00 Asia/Shanghai`，并可设置 `Persistent=true` 补跑停机期间错过的触发。

两种部署都在目标机配置凭据，并让采集器与 FastAPI 使用同一个 `CROWDING_FILE` 基础路径。每个模块保留一个写入任务，通过调度器退出状态和日志排查失败。

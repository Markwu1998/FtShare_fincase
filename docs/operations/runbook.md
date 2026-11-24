# 运行与运维手册

适用环境为 Windows PowerShell、已安装项目依赖的 Python 环境和具备相应权限的 RQData 账号。可沿用 conda `rqsdk`。
本手册覆盖：首次安装、日常运行、定时任务、数据目录、健康检查、故障处理、备份与升级。

> 所有命令都在项目根目录执行：
> 例如 `D:\ETFMonitor\FtShare_fincase`；请按目标机实际目录调整。

---

## 1. 系统构成

网页服务和盘中采集器分别运行。日频更新、拥挤度更新和历史复盘由独立命令发布文件，部署机可按需配置调度。

| 角色 | 入口 | 端口 | 读/写 | 说明 |
|---|---|---|---|---|
| 网页服务 | `scripts/python-backend.ps1 web` | 8080 | 只读 | FastAPI；ETF 页 `/`，拥挤度页 `/crowding` |
| 盘中采集器 | `scripts/python-backend.ps1 -Mode live -Arguments @('--watch')` | 无 | 只写 | RQData 推送；09:15 预载基准，09:30 订阅 |
| 日频更新 | `scripts/update-daily-all.ps1` | 无 | 只写 | 每日 08:20 总更新 / 12:30 补采；RQData + DDB 双源 |
| 逐 ETF 放量 | 由采集器自动写入 | 无 | 只写 | `etf-burst/live.json` + 每日归档 |
| 拥挤度 | `scripts/python-backend.ps1 crowding` | 无 | 只写 | 四个独立归档，按模块增量 |
| 主动成交与申赎对照 | `python -m etf_backend.flow_comparison <CSV>` | 无 | 导入写入 | 从专题回放 CSV 生成独立私有快照 |

**关键事实：**

- 网页**只读文件**，快照更新后按 `mtime` 自动热加载，**不需要重启**。
- 盘中缓存由采集器写，网页不直接访问 RQData；Tick 报价经 `/api/live/stream` 约每秒合并推送，失败时每 2 秒读缓存。正式放量默认使用供应商分钟 bar，Tick 聚合、候选与切换条件见 [Tick 监控](../reference/tick-driver.md)。
- 日频数据存在「披露延迟」：`08:20` 更新的是**上一交易日**，当天数据要等收盘后披露。
- 日切：`/api/live` 只返回**当日**（Asia/Shanghai）缓存；盘前或跨日后返回 503，页面自动刷新后盘中区域为空，09:30 新缓存到达后恢复。
- 页头新鲜度：显示最新行情分钟、本地时间和延时；盘中延时 >90 秒变黄，>180 秒变红告警；午休/收盘显示中性的“午间休市/已收盘”。
- 日频源可选 `RQData` 或 `DDB`，**当前默认 DDB**；页面按源读取对应 seed，不重启服务；DDB 的 `fund_etf_nr` 未覆盖全部核心 ETF 或行情日期落后时自动标记未就绪，`/api/board?source=ddb` 返回 409。
- 实时源独立：目前只有 `RQData`，页面保留实时源下拉，后续新增实时源时只需增加缓存与适配器。

---

## 2. 首次安装（一次性）

### 2.1 前置检查

```powershell
python -m pip install -r requirements-python.txt
python -c "import sys,rqdatac,rqdatac_fund,fastapi,uvicorn;print(sys.version.split()[0],rqdatac.__version__)"
```

安装与检查请使用同一个解释器。依赖版本见 `requirements-python.txt`。Windows 可通过 `ETF_BACKEND_PYTHON` 指定解释器。采集凭据可由 `RQDATAC2_CONF` 提供，或通过启动脚本读取 `RQDATA_CREDENTIAL_FILE` 指定的本机加密凭据。磁盘规划见[容量](#42-容量)。

### 2.2 确认 `.env`

```powershell
Get-Content .env | Select-String 'RQDATA_PYTHON|DATA_MODE|PORT|SEED|LIVE_CACHE|CROWDING'
```

关键项：

```dotenv
RQDATA_PYTHON=D:\anaconda\envs\rqsdk\python.exe
DATA_MODE=live
FTSHARE_SEED_FILE=data/private/rqdata-seed.json
FTSHARE_LIVE_CACHE_FILE=data/private/rqdata-live.json
CROWDING_FILE=data/private/rqdata-crowding.json
ETF_BURST_DIR=data/private/etf-burst
MARKET_DATA_CONFIG_FILE=.local/rqdata-market.json
PORT=8080
```

> `.env` 必须是 **UTF-8**（BOM 可有可无）。启动脚本已按 UTF-8 读取，中文注释安全；若存成 ANSI/GBK 会读错。

### 2.3 首次全量历史（换机器时才需要）

```powershell
& .\scripts\python-backend.ps1 history
```

已有快照且与当前成员、公式和分钟轴匹配时可以跳过，日常运行使用 `--recent`。成员或公式变动时按[核心篮子维护](#44-核心篮子维护)生成新的全量快照。

### 2.4 首次拥挤度（可选）

```powershell
& .\scripts\python-backend.ps1 crowding
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=industry')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=funds')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=financing')
```

### 2.5 8080 端口占用

```powershell
netstat -ano | findstr ":8080" | findstr LISTENING
```

用输出的 PID 核对已有服务。可以继续使用该实例，或在自己的启动终端按 Ctrl+C 后重启。需要并行运行时，设置 `$env:PORT = '8086'` 后用 `scripts/python-backend.ps1 web` 启动。`start-web.cmd` 的端口保护固定检查 8080。

### 2.6 定时任务

以下为目标部署机的建议调度配置。克隆、合并代码或普通启动不会注册任务；本文不表示当前电脑已注册 DSH 或 Windows 任务。Windows Server、Linux 或腾讯云 CVM 的调度方式见 [部署说明](deployment.md)。

| 建议任务 | 触发（Asia/Shanghai） | 动作 |
|---|---|---|
| `ETF 日频与拥挤度 08:20` | 周一至周五 08:20 | `update-daily-all.ps1`：RQData + DDB ETF + 日度拥挤度 + 融资 + 行业 |
| `ETF 周六公募拥挤度` | 周六 09:00 | 公募重仓（季度） |
| `ETF 日频补采 12:30` | 周一至周五 12:30 | `update-sources.ps1 -SkipCrowding`（RQData + 可选 DDB），补晚披露 |
| `ETF 开盘前启动 08:25` | 周一至周五 08:25 | 检查 8080，必要时启动网页与采集器，输出状态 |
| `ETF 收盘补全 15:10` | 周一至周五 15:10 | `live --close`，用当日 1m 线补全到 15:00 |

08:20 总更新任务同时构建 RQData 与 DDB，并更新拥挤度；12:30 仍保留 ETF/DDB 晚披露补采。

说明：

- 推荐目标服务器使用操作系统调度器直接调用脚本；DSH 等外部调度器可选，任务状态需在实际使用的调度器中检查。
- `setup-tasks.cmd` 是可选的 Windows 安装入口，只有显式运行才注册 4 个任务：08:20 与 12:30 执行 `update-daily.cmd`，登录时启动网页/采集器。它不包含上表的拥挤度、公募及收盘补全任务，也不会配置 09:20 总更新；完整方案须在目标机单独配置。
- 每个采集模块仅保留一个调度入口，避免重复启动或并发写入同一归档。

> 若 Windows 任务注册失败（受限环境会返回「拒绝访问」），可在「任务计划程序」手工创建：操作 `cmd.exe`，参数 `/c "完整路径\xxx.cmd"`，起始于项目根目录。

### 2.7 验证

```powershell
.\status.cmd
```

预期：`8080` 返回 `RQData / live`、快照日期正确、交易时段缓存有当日分钟。只有在目标机主动安装任务后才检查相应任务状态；本地未注册任务属正常情况。

---

## 3. 日常运行

### 3.1 自动时间表

| 北京时间 | 动作 | 谁负责 |
|---|---|---|
| 登录时 | 启动网页 + 采集器 | Windows 任务（可选）或手动 |
| 08:25 | 检查并启动网页/采集器 | DSH `ETF 开盘前启动 08:25` |
| 08:20 | RQData + DDB ETF + 日度拥挤度 + 融资 + 行业 | DSH `ETF 日频与拥挤度 08:20` |
| 周六 09:00 | 公募（季度）更新 | DSH `ETF 周六公募拥挤度` |
| 09:15–09:30 | 采集器预载 60 日分钟基准 | 采集器 |
| 09:30–15:02 | 实时推送，写盘中缓存 | 采集器 |
| 12:30 | 第二次日频增量（RQData + 可选 DDB，`-SkipCrowding`） | DSH `ETF 日频补采 12:30` |
| 15:10 | 用当日 1m 线补全盘中缓存到 15:00 | DSH `ETF 收盘补全 15:10` |
| 15:02 后 | 采集器空闲，等下一交易日 | 采集器 |

### 3.2 手动启动 / 停止

| 目的 | 操作 |
|---|---|
| 启动网页（前台可见日志） | 双击 `start.cmd` |
| 启动网页（任务用，带端口检查） | 双击 `start-web.cmd` |
| 启动采集器 | 双击 `start-collector.cmd`（内置心跳检查，已在运行会直接退出） |
| 停止 | 在对应控制台按 `Ctrl+C`，或任务管理器结束 `python.exe` |

### 3.3 每日检查清单

**开盘前（09:00 前）**

```powershell
.\status.cmd
```

- 网页 8080 返回 `mode=live`；
- 快照 `flow/market` 是上一交易日；
- 采集器已启动（09:15 后缓存会开始更新）。

**盘中（10:00 / 14:00）**

- `/api/live` 返回 200，`last_complete_minute` 在推进；
- 页面「ETF 异动雷达」显示盘中状态。

**收盘后（15:10 之后）**

- 当天分钟缓存的 `fetched_at` 是今天；
- 若 18:00 后份额/净值已披露，可手动补一次 `update-daily.cmd`。

### 3.4 单日 ETF 异动复盘

Windows 在项目根目录执行以下命令。填写完整交易日期，每次使用独立的输出目录。默认回放配置中的核心 ETF，并为对应的 IF、IH、IC、IM 读取目标日各四个实际挂牌合约。

```powershell
& .\scripts\python-backend.ps1 -Mode day-replay -Arguments @(
  '--day=2026-11-20', '--source=rqdata', '--out=.local/day-replay-20261120')
```

1. 脚本优先复用兼容基准，否则用目标日前 60 个交易日的分钟线和日线生成 20／60 日同窗与全天参考。额度有限时可另用独立目录，通过 `--symbols 510050.SH` 先试单只；下载中断后，用原命令与原输出目录续跑，已完成的标的不再请求。
2. 查看输出目录中的 `replay/日期.json` 和 `journals/日期.sqlite3`。先核对基准截止日、标的 Tick 与分钟覆盖、期货合约覆盖和秒级基准状态，再看预警、确认、金额及合约分布。ETF 异动金额按标的与分钟去重。期货主动卖成交额、净增仓名义额、卖侧增仓代理额分别判断；覆盖不足的窗口以归档状态为准。
3. 如需在网页查看，另开 PowerShell，把 `ETF_BURST_DIR` 指向该输出目录，使用空闲端口启动 `web`，在「ETF 异动雷达」选择日期和回放时点。示例端口为 8086。

   ```powershell
   $env:ETF_BURST_DIR = (Resolve-Path .local/day-replay-20261120).Path
   $env:DATA_MODE = 'live'
   $env:ENABLE_RADAR = '1'
   $env:PORT = '8086'
   & .\scripts\python-backend.ps1 web
   ```

已下载的数据可用 `--input-dir` 和 `--offline` 在新输出目录重跑。RQData 历史 Tick 默认按行情时间复盘；保存了真实接收时间的文件才可用接收时钟。30／60 秒候选另需目标日前的秒级基准。DDB 模式待 ETF 和股指期货完整 Tick 接口接入。输入字段、离线示例和基准准备见 [Tick 数据回放](../reference/tick-driver.md#tick-数据回放)。复盘按需运行。

雷达回放时点设为 15:00，可查看同篮子日末净申赎及覆盖数。份额未齐时，页面显示已覆盖小计，归档 `daily_flow.by_symbol` 保留逐标的金额和缺数原因。日末披露补齐后，沿用原 Tick 输入目录，用 RQData 联网模式和新的输出目录重跑。完全离线重跑时，将 `inputs/fund-flow.json` 与 Tick 输入一起提供。

### 3.5 日度主动成交与申赎对照

在 ETF 资金观测台的「历史资金」中选择「ETF 盘中主动成交与日末净申赎」。起止日期均包含，标的下拉可切到单只 ETF。「每日明细」「按标的汇总」「按日期汇总」共用筛选条件。

盘中主动买卖使用 RQData Tick 区间均价与上一档盘口中点的方向估算。统计范围为确认放量窗口的并集。日末净申赎为同标的同日的份额变化乘单位净值估算。差额等于主动买减日末净申赎。汇总金额仅累计三个字段均有效的记录，配对数标记汇总的覆盖。已回放且无确认放量的标的日，主动买卖为零。缺份额时，明细仍显示有效的盘中买卖金额，净申赎和差额留空。

用中点灰区专题回放的 `etf_daily_series.csv` 导入完整目标区间，字段见[数据与计算](../reference/flow-methodology.md#主动成交与净申赎对照)。标准 `day-replay` 写入单日雷达 JSON 和日志。主动成交对照使用独立的标的日 CSV。以下为本地 2026-08-20 至 2026-11-20 示例，使用项目的 Python 环境执行，命令会原子替换私有快照。`.local` 研究输出按授权随部署迁移。

```powershell
python -m etf_backend.flow_comparison .local/etf-direction-20260820-20261120/etf_daily_series.csv
```

网页从 `data/private/etf-flow-comparison.json` 读取已导入的日期。重新导入后刷新页面即可查看更新。迁移应用时将该快照放到目标项目的数据目录。

---

## 4. 数据与目录

### 4.1 目录结构

```
data/private/                     ← 已被 .gitignore 忽略
├─ rqdata-seed.json               历史快照（约 1.0 MB）
├─ rqdata-live.json               盘中缓存（约 18 KB，每轮覆盖）
├─ rqdata-crowding.json           拥挤度日度
├─ rqdata-crowding-industry.json  行业（日度）
├─ rqdata-crowding-funds.json     公募重仓（季度）
├─ rqdata-crowding-financing.json 融资宏观（日度）
├─ etf-flow-comparison.json       主动成交与净申赎专题快照
└─ etf-burst/
   ├─ live.json                   当日事件
   ├─ sessions/YYYY-MM-DD.json    每日归档
   ├─ replay/YYYY-MM-DD.json      回放归档
   ├─ baselines/YYYY-MM-DD.json   分钟基准
   ├─ tick-baselines/YYYY-MM-DD.json  秒级基准
   └─ journals/YYYY-MM-DD.sqlite3    事件及行情日志
```

### 4.2 容量

历史快照体量取决于历史范围、有效基金数与 `minute_days`。盘中缓存覆盖写入，每日事件、基准和 SQLite 日志按日期累积。以 2026-11-20 的 20 只 ETF 与 16 个期货合约复盘为例，JSON 约 1.5 MB，SQLite 约 41 MB，另有 Tick 输入缓存。实际留存容量应按所在环境的日志、回放输入和基准文件估算。备份细节见 [Tick 日志说明](../reference/tick-driver.md#日志与复盘)。

### 4.3 换目录

把四个变量指向任意目录（支持绝对路径），例如：

```dotenv
FTSHARE_SEED_FILE=D:\ftshare-data\rqdata-seed.json
FTSHARE_LIVE_CACHE_FILE=D:\ftshare-data\rqdata-live.json
CROWDING_FILE=D:\ftshare-data\rqdata-crowding.json
ETF_BURST_DIR=D:\ftshare-data\etf-burst
```

放在仓库外即不被 git 管理；放在仓库内请沿用 `data/private/`。

### 4.4 核心篮子维护

核心篮子只有一个来源：`config/basket.json`。不要直接改 `etf_backend/constants.json` 或 `src/server/model.js`。

```powershell
python scripts\basket.py list
python scripts\basket.py set --help
```

更换成分时用 `set` 一次提交完整名单。脚本校验恰好 20 只、无重复并都在 `config/universe.json` 中，然后同步：

- `src/server/model.js` 的 `BASKET`
- `config/universe.json` 的 `is_core_basket`
- `config/etf-burst.json` 若定义了 `symbols` 也一并更新（当前未定义，默认跟随篮子）

**篮子变更会改变日度净申赎历史，必须全量重建，不能用 `--recent`：**

```powershell
& .\scripts\python-backend.ps1 -Mode history -Arguments @('--seed=data/private/rqdata-seed-new.json')
$env:FTSHARE_SEED_FILE = 'data/private/rqdata-seed-new.json'
$env:DATA_MODE = 'live'
$env:PORT = '8086'
& .\scripts\python-backend.ps1 web
```

先在独立网页核对 `/api/sources` 和 `/api/board`，确认后将新路径用于部署配置。网页与采集器重启后加载最新篮子。收盘后可运行 `live --close` 重建当天缓存。历史校正资料可通过 `data/private/flow-recoveries.json` 保留。专题对照需用新篮子重新配对和导入，演示快照也需重新生成。

### 4.5 日频数据源构建

```powershell
# 双源顺序构建（RQData + DDB）
& .\scripts\python-backend.ps1 history -Arguments @('--source','rqdata','--recent')

# DDB（同样需要 DDB_PASSWORD；当前默认源）
& .\scripts\python-backend.ps1 history -Arguments @('--source','ddb','--recent')
```

- 源注册在 `config/data-sources.json`，默认 `rqdata`；
- DDB 构建前会检查核心 20 只的 `fund_etf_nr` 最后日期；不满足目标日期直接失败，不覆盖旧 seed；
- 页面读取 `/api/sources` 显示就绪状态，切换无需重启；
- 双源顺序构建可执行 `scripts/update-sources.ps1`（DDB 失败只告警，不阻断 RQData）。

DDB 环境变量见 `.env.example`：`DDB_HOST`、`DDB_PORT`、`DDB_USER`、`DDB_PASSWORD`。

---

## 5. 健康检查

### 5.1 一键状态

```powershell
.\status.cmd
```

输出包含 Windows 任务（可选）状态、8080/8081 接口、默认路径快照、缓存时点、采集器心跳、拥挤度行数和数据目录大小。自定义端口或文件时，直接检查实际部署地址及配置路径。
若目标机使用外部调度器，其任务不在此脚本里，需在实际调度器中查看。

### 5.2 接口判定

| 接口 | 正常表现 | 异常含义 |
|---|---|---|
| `/api/health` | `mode=live`、`provider=RQData` | `demo` = 用错进程或 `.env` |
| `/api/board` | 200，按披露进度显示 `latest_flow_date` | 409 = 所选日频源未就绪，先查 `/api/sources`；503 = 就绪检查后仍读不到有效快照 |
| `/api/live` | 当日有缓存时 200，Tick 时间或 `last_complete_minute` 在推进 | 503 = 无有效当日缓存；首个完整分钟前该分钟字段可为空，收盘后当日缓存仍可读取 |
| `/api/crowding` | 200，`rows` 非空 | 503 = 尚未采集 |
| `/api/etf-burst/dates` | 200，`dates` 数组 | 404 = 雷达未启用 |
| `/api/etf-flow-comparison` | 200，`rows` 为按标的日归档的记录 | 503 = 专题快照未就绪或为演示模式 |

### 5.3 「数据没更新」排查顺序

1. `.\status.cmd` 看快照 `built_at` 是否变了；
2. 看 `.local\update-daily.log` 最后几行；
3. 看计划任务「上次运行结果」；
4. 手动跑一次 `update-daily.cmd`，观察是否报 `ValueError`（覆盖不足）或认证失败；
5. 若份额/净值尚未披露，等 12:30 或次日。

---

## 6. 故障处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 8080 打开是演示数据 | 残留的 demo 进程占用 | 重启；或结束对应 `python.exe` |
| `start-web.cmd` 直接退出 | 8080 已被占用（保护逻辑） | 正常；已有实例在跑 |
| `PSSecurityException` | PowerShell 执行策略 | 用 `.cmd` 包装；或 `Set-ExecutionPolicy -Scope Process Bypass -Force` |
| `.env` 修改不生效 | ① shell 已有同名变量（脚本不覆盖）② 非 UTF-8 保存 | `Remove-Item Env:变量名`；另存为 UTF-8 |
| `ModuleNotFoundError: rqdatac_fund` | 解释器不是 rqsdk | 检查 `.env` 的 `RQDATA_PYTHON` |
| `history ... (ValueError)` | 覆盖检查不通过（区间太短/披露未到） | 等 12:30 再跑；或加长 `flow_start` |
| 旧快照存在而 `/api/board` 返回 409 | 快照成员与 `config/basket.json` 不同 | 用新的 `--seed` 路径全量重建并验证，见 4.4 |
| `/api/live` 503 | 无有效当日缓存、跨日或回放未授权 | 启动采集器并检查缓存日期；收盘本身不强制返回 503 |
| 雷达无「单只 ETF 放量」 | 非交易时段 / 当日无事件 / 雷达未启用 | 检查 `ENABLE_RADAR=1`；盘中再看 |
| 采集器日志 `outside_trading_session` | 非交易时段 | 正常，等开盘 |
| 重复运行 `start-collector.cmd` | 心跳未过期（5 分钟内） | 脚本直接退出，属正常保护 |
| 采集器中断后不恢复 | 网络/睡眠 | 代码有 45 秒看门狗会自动重连；仍失败则重启任务 |
| 任务「上次运行结果」非 0 | 数据/网络/凭据 | 看 `.local\update-daily.log`，手动重跑定位 |
| 磁盘不足 | 长期归档 | 清理 `etf-burst/sessions`、`replay` 旧日期 |

---

## 7. 备份、恢复与升级

备份、迁移、升级与回滚步骤统一见 [备份、恢复与升级](backup-and-upgrade.md)。

---

## 8. 安全

- **不要**把 8080 直接暴露到公网：FastAPI 没有认证与限流（仅反向代理层可加）。
- 对外请用 nginx/Caddy + HTTPS + 访问控制；SSE（`/api/etf-burst/stream`）需关闭代理缓冲。
- RQData 凭据只在采集侧；网页进程不需要 `RQDATAC2_CONF`。
- 数据目录与 `.env` 均不入 git；提交前用 `git status` 核对。

---

## 9. 命令速查

```powershell
# 状态
.\status.cmd

# 网页（前台可见日志）
.\start.cmd

# 网页（任务用，带端口保护）
.\start-web.cmd

# 盘中采集（常驻）
.\start-collector.cmd

# 日频增量
.\update-daily.cmd

# 手动命令形式
& .\scripts\python-backend.ps1 history -Arguments @('--recent')
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--watch')
& .\scripts\python-backend.ps1 web

# ETF 异动单日复盘（独立归档，见 3.4）
& .\scripts\python-backend.ps1 -Mode day-replay -Arguments @('--day=2026-11-20','--source=rqdata')

# 分时面板回放（独立缓存）
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--replay=2026-11-13','--cache=.local\rqdata-replay.json')
& .\scripts\python-backend.ps1 web -ReplayCache '.local\rqdata-replay.json'

# 单测
& "D:\anaconda\envs\rqsdk\python.exe" -m unittest discover -s tests -p "test_*.py"

# 计划任务
schtasks /Query /TN FtShare-UpdateDaily /FO LIST
Unregister-ScheduledTask -TaskName FtShare-UpdateDaily -Confirm:$false
```

---

## 10. 本版变更记录

| 变更 | 说明 |
|---|---|
| 日频增量 `history --recent` | 只重查最近 14 天并合并，不再全量重跑 |
| 公募持仓兼容 | `rqdatac_fund` 对未知基金代码二分降级，避免整批失败 |
| rqdatac 2.x 兼容 | 分钟索引层级 `datetime` / `date` 自适应 |
| 端口统一 8080 | README、文档、启动脚本、`.env.example` 一致 |
| `.env` UTF-8 | 启动脚本按 UTF-8 读取，中文注释不再吞行 |
| 运维脚本 | `start-web.cmd`、`start-collector.cmd`、`update-daily.cmd`、`setup-tasks.cmd`、`status.cmd` |
| DSH 定时任务 | 08:20 总更新、12:30 补采、08:25 开盘前启动 |
| 采集器心跳 | `data/private/collector.heartbeat`，`start-collector.cmd` 据此防止重复启动 |

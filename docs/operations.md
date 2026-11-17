# 运行与运维手册

适用环境：Windows 10/11 + conda 环境 `rqsdk`（Python 3.9）+ 具备权限的 RQData 账号。
本手册覆盖：首次安装、日常运行、定时任务、数据目录、健康检查、故障处理、备份与升级。

> 所有命令都在项目根目录执行：
> `C:\Users\Lenovo\PycharmProjects\FtShare_fincase`

---

## 1. 系统构成

项目由「两个常驻进程 + 一个定时更新 + 四个数据文件」组成。

| 角色 | 入口 | 端口 | 读/写 | 说明 |
|---|---|---|---|---|
| 网页服务 | `scripts/python-backend.ps1 web` | 8080 | 只读 | FastAPI；ETF 页 `/`，拥挤度页 `/crowding` |
| 盘中采集器 | `scripts/python-backend.ps1 -Mode live -Arguments @('--watch')` | 无 | 只写 | RQData 推送；09:15 预载基准，09:30 订阅 |
| 日频更新 | `scripts/python-backend.ps1 history -Arguments @('--recent')` | 无 | 只写 | 每日 08:20 / 12:30，滚动补齐上一交易日 |
| 逐 ETF 放量 | 由采集器自动写入 | 无 | 只写 | `etf-burst/live.json` + 每日归档 |
| 拥挤度 | `scripts/python-backend.ps1 crowding` | 无 | 只写 | 四个独立归档，按模块增量 |

**关键事实：**

- 网页**只读文件**，快照更新后按 `mtime` 自动热加载，**不需要重启**。
- 盘中缓存由采集器写，网页不直接访问 RQData。
- 日频数据存在「披露延迟」：`08:20` 更新的是**上一交易日**，当天数据要等收盘后披露。

---

## 2. 首次安装（一次性）

### 2.1 前置检查

```powershell
& "D:\anaconda\envs\rqsdk\python.exe" -c "import sys,rqdatac,rqdatac_fund,fastapi,uvicorn;print(sys.version.split()[0],rqdatac.__version__)"
```

预期：`3.9.16 2.10.14.2 ...`。
凭据来自用户级环境变量 `RQDATAC2_CONF`，任何新终端都可用，无需 `conda activate`。

磁盘预留：约 3 MB/年（详见第 4 节）。

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

已有 `data/private/rqdata-seed.json` 时跳过。首次约数分钟；之后只用 `--recent`。

### 2.4 首次拥挤度（可选）

```powershell
& .\scripts\python-backend.ps1 crowding
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=industry')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=funds')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=financing')
```

### 2.5 释放 8080

```powershell
netstat -ano | findstr ":8080" | findstr LISTENING
```

若有残留进程：重启电脑最稳妥；或在任务管理器结束对应 `python.exe`。
**8080 被占时网页任务会跳过启动**（`start-web.cmd` 有端口检查）。

### 2.6 定时任务

日频更新与开盘前启动已注册到 **DSH 定时任务**（不在 Windows 任务计划里）：

| DSH 任务 | 触发（Asia/Shanghai） | 动作 |
|---|---|---|
| `ETF 日频更新 08:20` | 周一至周五 08:20 | `history --recent`，报告快照日期 |
| `ETF 日频补采 12:30` | 周一至周五 12:30 | 同上，补晚披露 |
| `ETF 开盘前启动 08:25` | 周一至周五 08:25 | 检查 8080，必要时启动网页与采集器，输出状态 |
| `ETF 收盘补全 15:10` | 周一至周五 15:10 | `live --close`，用当日 1m 线补全到 15:00 |

说明：

- DSH 任务以「新会话 + 提示词」方式执行，需要 **DSH 运行时处于运行状态**；停机期间错过的触发只会补发最近一次。
- 网页与采集器也可以改用 Windows 任务计划（可选）：双击 `setup-tasks.cmd`，注册 4 个 Windows 任务。
- **两种方式二选一**，不要同时启用，避免网页/采集器被重复启动。

> 若 Windows 任务注册失败（受限环境会返回「拒绝访问」），可在「任务计划程序」手工创建：操作 `cmd.exe`，参数 `/c "完整路径\xxx.cmd"`，起始于项目根目录。

### 2.7 验证

```powershell
.\status.cmd
```

预期：4 个任务状态正常、`8080` 返回 `RQData / live`、快照日期正确、缓存有当日分钟。

---

## 3. 日常运行

### 3.1 自动时间表

| 北京时间 | 动作 | 谁负责 |
|---|---|---|
| 登录时 | 启动网页 + 采集器 | Windows 任务（可选）或手动 |
| 08:20 | 日频增量更新（上一交易日） | DSH `ETF 日频更新 08:20` |
| 08:25 | 检查并启动网页/采集器 | DSH `ETF 开盘前启动 08:25` |
| 09:15–09:30 | 采集器预载 60 日分钟基准 | 采集器 |
| 09:30–15:02 | 实时推送，写盘中缓存 | 采集器 |
| 12:30 | 第二次日频增量（补晚披露） | DSH `ETF 日频补采 12:30` |
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

---

## 4. 数据与目录

### 4.1 目录结构

```
data/private/                     ← 已被 .gitignore 忽略
├─ rqdata-seed.json               历史快照（约 1.0 MB）
├─ rqdata-live.json               盘中缓存（约 18 KB，每轮覆盖）
├─ rqdata-crowding.json           拥挤度日度
├─ rqdata-crowding-industry.json  行业（周度）
├─ rqdata-crowding-funds.json     公募重仓（季度）
├─ rqdata-crowding-financing.json 融资宏观（日度）
└─ etf-burst/
   ├─ live.json                   当日事件
   ├─ sessions/YYYY-MM-DD.json    每日归档
   └─ replay/YYYY-MM-DD.json      回放归档
```

### 4.2 容量

当前总计约 **1.5 MB**。

| 部分 | 规模 | 增长 |
|---|---|---|
| 历史快照 | 1.0 MB | +1.1 KB/交易日 ≈ 0.26 MB/年 |
| 分钟样本 | 0.43 MB | 固定 66 天（`minute_days`），不随年份增长 |
| 拥挤度四文件 | 0.46 MB | 约 0.4 MB/年 |
| 盘中缓存 | 18 KB | 覆盖写，不累积 |
| 放量归档 | 7 KB/交易日 | ≈ 1.7 MB/年 |

**结论：一年约 2–3 MB，无需清理也能跑很多年。** 如需瘦身，只删 `etf-burst/sessions/` 与 `replay/` 里的旧日期即可。

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
python scripts\basket.py set 510050.SH 510100.SH ...   # 直接替换为 20 只
python scripts\basket.py add 510330.SH                 # 增一只；需同时删一只以保持 20
python scripts\basket.py remove 159633.SZ
```

脚本会校验“恰好 20 只、不重复、都在 `config/universe.json` 中”，并同步：

- `src/server/model.js` 的 `BASKET`
- `config/universe.json` 的 `is_core_basket`
- `config/etf-burst.json` 若定义了 `symbols` 也一并更新（当前未定义，默认跟随篮子）

**篮子变更会改变日度净申赎历史，必须全量重建，不能用 `--recent`：**

```powershell
Move-Item data\private\rqdata-seed.json .local\rqdata-seed-old.json
& .\scripts\python-backend.ps1 history
npm run demo:generate
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--close')
```

然后**重启网页与采集器**（`BASKET` 在进程启动时加载），最后 `.\status.cmd` 验证。

---

## 5. 健康检查

### 5.1 一键状态

```powershell
.\status.cmd
```

输出包含：Windows 任务（可选）状态、8080/8081 接口、快照日期、缓存时点、采集器心跳、拥挤度行数、数据目录大小。
DSH 定时任务不在此脚本里，需在 DSH 的调度器或本会话的定时任务列表中查看。

### 5.2 接口判定

| 接口 | 正常表现 | 异常含义 |
|---|---|---|
| `/api/health` | `mode=live`、`provider=RQData` | `demo` = 用错进程或 `.env` |
| `/api/board` | `latest_flow_date` 为上一交易日 | 503 = 快照缺失/损坏 |
| `/api/live` | 200，`last_complete_minute` 在推进 | 503 = 采集器未运行或已收盘 |
| `/api/crowding` | 200，`rows` 非空 | 503 = 尚未采集 |
| `/api/etf-burst/dates` | 200，`dates` 数组 | 404 = 雷达未启用 |

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
| `/api/live` 503 | 采集器未启动 / 非交易时段 / 尚未 09:30 | 启动采集器；收盘后属正常 |
| 雷达无「单只 ETF 放量」 | 非交易时段 / 当日无事件 / 雷达未启用 | 检查 `ENABLE_RADAR=1`；盘中再看 |
| 采集器日志 `outside_trading_session` | 非交易时段 | 正常，等开盘 |
| 重复运行 `start-collector.cmd` | 心跳未过期（5 分钟内） | 脚本直接退出，属正常保护 |
| 采集器中断后不恢复 | 网络/睡眠 | 代码有 45 秒看门狗会自动重连；仍失败则重启任务 |
| 任务「上次运行结果」非 0 | 数据/网络/凭据 | 看 `.local\update-daily.log`，手动重跑定位 |
| 磁盘不足 | 长期归档 | 清理 `etf-burst/sessions`、`replay` 旧日期 |

---

## 7. 备份、恢复、迁移

### 7.1 备份

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
Copy-Item data\private "D:\ftshare-backup\data-$stamp" -Recurse
Copy-Item .env "D:\ftshare-backup\env-$stamp.txt"
Copy-Item .local\rqdata-market.json "D:\ftshare-backup\rqdata-market-$stamp.json"
```

建议每次大版本升级前、以及每月一次。

### 7.2 恢复

1. 停网页与采集器；
2. 把备份的 `data/private` 覆盖回去；
3. 恢复 `.env`；
4. 启动网页，`status.cmd` 核对日期；
5. 需要时跑一次 `history --recent` 补齐。

### 7.3 迁移到新机器

1. 安装 conda 环境与 `requirements-python.txt`；
2. 配置 `RQDATAC2_CONF`（RQData 凭据）；
3. 复制 `data/private`、`.env`、`.local/rqdata-market.json`；
4. 注册计划任务（`setup-tasks.cmd`）；
5. `status.cmd` 验证。

---

## 8. 升级与回滚

- 快照带 `schema:1` 与 `formula` 版本；**跨公式版本不能直接混用**，需全量重建。
- 升级 `rqdatac` / `rqdatac_fund` 前先备份：

```powershell
& "D:\anaconda\envs\rqsdk\python.exe" -m pip freeze > .local\rqsdk-before.lock.txt
```

- 升级后必须复测：

```powershell
& "D:\anaconda\envs\rqsdk\python.exe" -m unittest discover -s tests -p "test_*.py"
& .\scripts\python-backend.ps1 history -Arguments @('--recent')
```

- 回滚：还原 `data/private` + `pip install -r .local\rqsdk-before.lock.txt`。
- 网页升级不需要重启数据采集；但 `etf_backend` 代码改动需重启网页进程。

---

## 9. 安全

- **不要**把 8080 直接暴露到公网：FastAPI 没有认证与限流（仅反向代理层可加）。
- 对外请用 nginx/Caddy + HTTPS + 访问控制；SSE（`/api/etf-burst/stream`）需关闭代理缓冲。
- RQData 凭据只在采集侧；网页进程不需要 `RQDATAC2_CONF`。
- 数据目录与 `.env` 均不入 git；提交前用 `git status` 核对。

---

## 10. 命令速查

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

# 回放（独立缓存）
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--replay=2026-11-13','--cache=.local\rqdata-replay.json')
& .\scripts\python-backend.ps1 web -ReplayCache '.local\rqdata-replay.json'

# 单测
& "D:\anaconda\envs\rqsdk\python.exe" -m unittest discover -s tests -p "test_*.py"

# 计划任务
schtasks /Query /TN FtShare-UpdateDaily /FO LIST
Unregister-ScheduledTask -TaskName FtShare-UpdateDaily -Confirm:$false
```

---

## 11. 本版变更记录

| 变更 | 说明 |
|---|---|
| 日频增量 `history --recent` | 只重查最近 14 天并合并，不再全量重跑 |
| 公募持仓兼容 | `rqdatac_fund` 对未知基金代码二分降级，避免整批失败 |
| rqdatac 2.x 兼容 | 分钟索引层级 `datetime` / `date` 自适应 |
| 端口统一 8080 | README、文档、启动脚本、`.env.example` 一致 |
| `.env` UTF-8 | 启动脚本按 UTF-8 读取，中文注释不再吞行 |
| 运维脚本 | `start-web.cmd`、`start-collector.cmd`、`update-daily.cmd`、`setup-tasks.cmd`、`status.cmd` |
| DSH 定时任务 | 08:20 更新、12:30 补采、08:25 开盘前启动 |
| 采集器心跳 | `data/private/collector.heartbeat`，`start-collector.cmd` 据此防止重复启动 |

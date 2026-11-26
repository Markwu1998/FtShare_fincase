# 数据管线

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

采集器负责历史快照、盘中缓存、拥挤度归档和逐 ETF 放量事件；网页只读取这些文件。

## 数据文件

| 文件 | 内容 | 更新方式 |
|---|---|---|
| `data/private/rqdata-seed.json` | 历史快照（约 1.0 MB） | `history` / `history --recent` |
| `data/private/rqdata-live.json` | 盘中分钟缓存（约 18 KB） | `live --watch`，每轮覆盖 |
| `data/private/rqdata-crowding*.json` | 拥挤度日度、行业、公募、融资 | `crowding` |
| `data/private/etf-burst/live.json` | 当日逐 ETF 放量事件 | 采集器自动写入 |
| `data/private/etf-burst/sessions/` | 每日事件归档 | 采集器自动写入 |
| `data/private/etf-flow-comparison.json` | 主动成交与净申赎专题快照 | `etf_backend.flow_comparison` 导入 |

## 历史快照与增量更新

首次构建：

```powershell
& .\scripts\python-backend.ps1 history
```

每日收盘后只更新最近窗口：

```powershell
& .\scripts\python-backend.ps1 history -Arguments @('--recent')
```

- `--recent` 只重查最近 14 天重叠窗口（含上游修订），再与现有快照按日期合并。
- 旧日期原样保留，窗口内用新值替换。
- 成员、公式、分钟轴不一致，或合并会丢失旧的有效交易日时，命令直接失败且不覆盖文件。
- 首次仍需一次全量 `history`。

## 收盘补全

`live --close` 用当日 1m 线重建完整盘中快照（不带 replay 标记），修复采集器晚启动或断线造成的 14:51–15:00 缺口：

```powershell
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--close')
```

期望输出包含 `"through": "15:00"`。它只改盘中缓存，不动日频历史。

## 回放

```powershell
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--replay=2026-11-13','--cache=.local\python-rqdata-replay.json')
& .\scripts\python-backend.ps1 web -ReplayCache '.local\python-rqdata-replay.json'
```

逐 ETF 放量也支持独立回放：

```powershell
& .\scripts\python-backend.ps1 -Mode burst-replay -Arguments @('--start=2026-11-12','--end=2026-11-13')
```

已有 RQData 成交额导出时，可以用 `--burst-input` 离线回放。输入包含 `schema_version:2`、`metric:"turnover"`、`unit:"CNY"`，以及 `1m` 和 `1d` 两组 `[symbol, date, slot, amount]`；`amount` 为元，分钟与日线须同口径。实时和历史快照分目录保存。

需要单日 ETF 和期货 Tick 同窗、日末申赎与 SQLite 日志时使用 `day-replay`，命令与离线输入见 [Tick 数据回放](../reference/tick-driver.md#tick-数据回放)。日度主动成交对照由 `flow_comparison` 从 `data/private/flow-replay/<日期>/replay` 增量合并：22:00 生成 DDB replay（同时镜像到 `data/private/etf-burst/replay` 供 UI 回放），09:10 等待上一交易日 20/20 核心份额/净值就绪后再补齐；筛选与操作见[日度对照操作](runbook.md#35-日度主动成交与申赎对照)。

## 定时任务

当前由 DSH 定时任务驱动：

| 任务 | 触发（Asia/Shanghai） | 动作 |
|---|---|---|
| `ETF 日频与拥挤度更新 09:10` | 周一至周五 09:10 | `update-daily-all.ps1`（RQData + DDB + 拥挤度 + flow comparison） |
| `ETF 开盘前启动 09:14` | 周一至周五 09:14 | 检查 8080，必要时启动网页/采集器 |
| `ETF 日频补采 12:30` | 周一至周五 12:30 | `update-sources.ps1 -SkipCrowding` |
| `ETF 收盘补全 15:10` | 周一至周五 15:10 | 已停用；由夜间 DDB 复盘替代 |
| `ETF 夜间 DDB 复盘 22:00` | 周一至周五 22:00 | `update-flow-replay.ps1 -Source ddb` + 当日 flow comparison |
| `ETF 周六公募拥挤度` | 周六 09:00 | 公募重仓（季度） |

采集器为常驻进程：09:15 自动切换新交易日，15:02 后空闲；每天写 `data/private/collector.heartbeat`，`start-collector.cmd` 依据心跳避免重复启动。

## 体量参考

历史快照体量取决于历史范围、有效基金数与 `minute_days`；盘中缓存覆盖写入，事件归档、基准、Tick 输入与 SQLite 日志按日期累积。以 20 只 ETF 单日复盘为例，JSON 约 1.5 MB、SQLite 约 41 MB。磁盘规划与备份见[容量](runbook.md#42-容量)。

## 相关文档

- [数据契约](../reference/data-contract.md)
- [净申赎与资金口径](../reference/flow-methodology.md)
- [数据源切换](../reference/data-sources/switching.md)
- [运维手册](runbook.md)

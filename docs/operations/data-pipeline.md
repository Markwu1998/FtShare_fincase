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

已有 RQData 成交量导出时，可用 `--burst-input` 离线回放。实时和历史快照分目录保存。

## 定时任务

当前由 DSH 定时任务驱动：

| 任务 | 触发（Asia/Shanghai） | 动作 |
|---|---|---|
| `ETF 日频与拥挤度 08:20` | 周一至周五 08:20 | `update-daily-all.ps1`（RQData + DDB） |
| `ETF 开盘前启动 08:25` | 周一至周五 08:25 | 检查 8080，必要时启动网页/采集器 |
| `ETF 日频补采 12:30` | 周一至周五 12:30 | `update-sources.ps1 -SkipCrowding` |
| `ETF 收盘补全 15:10` | 周一至周五 15:10 | `live --close` |
| `ETF 周六公募拥挤度` | 周六 09:00 | 公募重仓（季度） |

采集器为常驻进程：09:15 自动切换新交易日，15:02 后空闲；每天写 `data/private/collector.heartbeat`，`start-collector.cmd` 依据心跳避免重复启动。

## 体量参考

362 只 ETF、2025 年起的快照约 1.0 MB，每个交易日新增约 1.1 KB（约 0.26 MB/年）；盘中缓存约 18 KB；逐 ETF 放量归档约 7 KB/交易日。一年合计约 2–3 MB。

## 相关文档

- [数据契约](../reference/data-contract.md)
- [净申赎与资金口径](../reference/flow-methodology.md)
- [数据源切换](../reference/data-sources/switching.md)
- [运维手册](runbook.md)

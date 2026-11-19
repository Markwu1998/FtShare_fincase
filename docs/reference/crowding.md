# 市场拥挤度

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

`/crowding` 提供独立的市场拥挤度观测台，读取 `/api/crowding?days=250&table_days=5`。查询范围按交易日计，实际日期以已归档数据为准。历史分位使用截至各日近五年内的有效样本，市值比值均值和标准差使用当前图表区间。

## 四个模块

| 模块 | 指标 | 数据源与频率 |
|---|---|---|
| 成交额集中度 / 大小盘风格 | 前 1%／3%／5% 个股成交额占比；沪深 300／中证 1000 市值与成交额比值 | RQData，日度 |
| 行业拥挤度 | 申万一级行业市值与成交额 | RQData，每日 |
| 融资与估值 | 沪深融资余额；名义 GDP、M2 | RQData，每日 |
| 公募重仓 | 主动公募季度前十大 A 股持仓 | RQData，季度 |

## 采集

首次采集默认回溯约一年。后续执行会补齐新日期并重查最近五个已归档交易日，写入独立快照：

```powershell
& .\scripts\python-backend.ps1 crowding
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=industry')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=funds')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=financing')
```

文件：

```text
data/private/rqdata-crowding.json            日度集中度/风格
data/private/rqdata-crowding-industry.json   行业，日度
data/private/rqdata-crowding-funds.json      公募重仓，季度
data/private/rqdata-crowding-financing.json  融资与估值，日度
```

采集器只发布北京时间当天之前的已结束交易日。尚无快照时对应 API 返回 503；采集异常时保留上次归档。

## 指标口径

- **指数行业**：使用 RQData 申万一级分类。周市值用周末成分，周成交额逐日按有效成分汇总。当前展示每期第一行业及峰值。
- **公募**：按 RQData 聚源 1101、1201 分类，合并有效多份额关系，取每只基金首次披露的前十大股中的 A 股。无法归类的持仓市值计入「未分类」。报告期用于横轴，公告日及基金覆盖独立披露。
- **融资**：使用沪深两市融资余额，市值为沪深 A 股本身的市值。名义 GDP 与 M2 按公布日期对齐。住户存款预留 Wind API 数据位。

## 定时任务

| 北京时间 | 模块 | 命令 |
|---|---|---|
| 每天 08:20 | 成交额集中度 / 大小盘风格 + 融资 + 行业 | `update-daily-all.ps1`（同时构建 RQData/DDB ETF） |
| 每周六 09:00 | 公募基金重仓集中度 | `python -m etf_backend.collect crowding --crowding-module=funds` |

Windows 可用任务计划程序；Linux 可用 systemd oneshot + timer，`OnCalendar` 显式指定 `Asia/Shanghai`。采集器与 FastAPI 必须使用同一个 `CROWDING_FILE` 基础路径。

## 数据源决策

**拥挤度固定使用 RQData，不跟随 ETF 资金观测台的日频源选择，也不做整页 RQData/DDB 切换。**

原因：DDB 无主动公募季度持仓、缺 M2/GDP、申万行业名称/层级映射不完整。完整核验与重新评估条件见归档文档 [拥挤度 RQData/DDB 核验](../archive/verification/crowding-ddb-20261118.md)。

## 相关文档

- [HTTP API](http-api.md)
- [数据契约](data-contract.md)
- [数据源切换](data-sources/switching.md)

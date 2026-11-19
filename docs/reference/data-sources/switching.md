# 日频数据源切换

> 状态：现行（已实现）· 适用版本：v0.1.x · 最后核对：2026-11-19

ETF 资金观测台支持 **RQData / DDB** 两个日频来源，默认 **DDB**。切换是读取已构建的快照，不在请求里触发采集，也不需要重启服务。

完整设计稿见归档 [日频数据源可切换改造设计](../../archive/design/data-source-switching.md)。

## 源注册

`config/data-sources.json` 定义日频源、默认源和实时源：

```json
{
  "default": "ddb",
  "realtime_default": "rqdata",
  "sources": [
    {"id": "rqdata", "label": "RQData", "kind": "daily", "seed": "data/private/rqdata-seed.json"},
    {"id": "ddb", "label": "DDB", "kind": "daily", "seed": "data/private/ddb-seed.json",
     "minute_seed": "data/private/rqdata-seed.json"}
  ],
  "realtime_sources": [
    {"id": "rqdata", "label": "RQData", "kind": "realtime", "cache": "data/private/rqdata-live.json"}
  ]
}
```

## 选择语义

1. 默认源为 `ddb`。
2. 页面通过 `/api/sources` 展示每个源的 `ready` / `reason`。
3. 用户切换后，日频请求带 `?source=ddb`；**一个请求只读一个 seed**。
4. 实时源独立，当前只有 RQData；切换日频不影响 `/api/live`。
5. 每个源独立构建、独立校验、独立记录 `built_at`、`latest_flow_date` 与能力。

## 就绪判断

DDB 的 `ready` 规则：

```text
20 只核心 ETF 的 fund_etf_nr.max(TRADE_DATE) >= 目标 flow 日期
且每只核心都有该日期数据
且 mkt_fundd 行情日期 >= 目标日期
```

不满足时：

- `/api/sources` 标 `ready=false` + `reason`；
- UI 的 DDB 按钮置灰；
- 强制 `?source=ddb` 返回 `409 source_not_ready`，不返回旧 DDB 数据。

## 能力矩阵

| 能力 | RQData | DDB |
|---|---|---|
| 收盘价、成交额、成交量、指数 | ✅ | ✅ |
| 单位净值、折溢价 | ✅ | ✅ |
| 总份额、净申赎、规模 | ✅ | 依赖 `fund_etf_nr` 保持更新 |
| 拆分/折算事件 | ✅ | 依赖 `fund_div` 保持更新 |
| 交易日历 | ✅ | 需确认 `md_trade_cal` 或改用行情日期 |
| 实时行情 | ✅ | 本期不接 |

## 构建

```powershell
# 同时构建两个源（当前 08:20 定时任务使用）
& .\scripts\update-daily-all.ps1

# 单独构建 RQData
& .\scripts\python-backend.ps1 history -Arguments @('--source','rqdata','--recent')

# 单独构建 DDB（需要 DDB_PASSWORD）
& .\scripts\python-backend.ps1 history -Arguments @('--source','ddb','--recent')
```

`update-daily-all.ps1` 同时构建 RQData、DDB，并更新拥挤度；`update-sources.ps1` 用于只构建两个 ETF 日频源。DDB 环境变量见 `.env.example`。

## 不适用范围

市场拥挤度**固定使用 RQData**，不跟随本切换；原因见 [市场拥挤度](../crowding.md)。

## 相关文档

- [RQData 数据源](rqdata.md)
- [DDB 数据源](ddb.md)
- [数据契约](../data-contract.md)

# RQData 数据源

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

RQData 是可选日频源，也是当前唯一的实时源；ETF 日频默认源已切换为 DDB。接口权限与迁移过程见归档的 [FtShare → RQData 迁移核验](../../archive/verification/ftshare-to-rqdata-20261114.md)。

## 代码与单位

- 行情使用交易所后缀，例如 `000001.XSHG`、`510050.XSHG`。
- 基金 SDK 使用六位基金代码，例如 `510050`。
- 成交额入应用前为人民币元，图表显示亿元。
- 价格统一使用 `adjust_type="none"`（不复权）。

## 接口映射

| 用途 | RQData 方法与字段 |
|---|---|
| 分时成交额、指数走势、累计成交、20/60 日雷达 | 盘中：`LiveMarketDataClient` 的 `bar_`/`tick_` 推送；启动或断线时用 `get_price(frequency="1m", adjust_type="none")` 补齐；历史样本用 `get_price("1m")`，统一 240 点时间轴 |
| ETF 表格、盘中总览 | `current_snapshot` 的 `last` 和 `total_turnover`，涨跌幅按上一交易日日线收盘计算 |
| ETF 历史成交额、指数日线和各标的前收 | `get_price(frequency="1d", adjust_type="none")`，取 `total_turnover`、`close` |
| 核心与宽基净申赎、分类表、放量与常态日划分 | `rqdatac_fund.fund.get_daily_units`、`fund.get_nav`、`fund.get_split` |
| 宽基日末规模 | `fund.get_daily_units` 的同日份额 × `fund.get_nav` 的原始单位净值 |
| 宽基收盘折溢价 | `get_price(1d)` 的 `close` 与 `volume`，以及 `fund.get_nav` 的同日原始单位净值 |
| 交易日及日期窗口 | `get_trading_dates` |
| 成员、上市日期和分类 | 项目配置 `config/universe.json` |
| ETF 基础信息、折算 | `instruments`、`fund.instruments`、`fund.get_split` |
| PCF 现金差额、成分证券及净值 | `etf.get_cash_components`、`etf.get_components`（份额单位与 `pre_date` 需核对） |

## 分钟轴

RQData 分钟轴为北京时间 09:31–11:30、13:01–15:00。历史与当日快照使用同一时间轴，不同分钟口径的快照隔离。

## 权限与额度

- 实盘需要具备相应权限的 RQData 账号；部分 FtShare 旧接口返回 403，已改用 RQData 等价接口。
- 网页不持有凭据，也不直接调用 RQData。
- 采集失败时保留上次快照，不用旧份额静默回退。

## 本地配置

```json
{"provider": "RQData", "minute_days": 66, "fund_history": true}
```

默认读取 `.local/rqdata-market.json`，可由 `MARKET_DATA_CONFIG_FILE` 覆盖；日期范围也可覆盖 `config/collection.json`。

## 相关文档

- [数据源切换](switching.md)
- [DDB 数据源](ddb.md)
- [图表数据接口](../chart-api.md)

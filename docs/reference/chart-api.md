# 图表数据接口

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

| 图表 | RQData 方法与字段 |
|---|---|
| 分时成交额、指数走势、累计成交、20/60 日雷达 | 盘中：`LiveMarketDataClient` 的 `bar_`/`tick_` 推送；启动或断线时用 `rqdatac.get_price(frequency="1m", adjust_type="none")` 补齐；历史样本用 `get_price("1m")`，统一 240 点时间轴 |
| ETF 表格、盘中总览 | `rqdatac.current_snapshot` 的 `last` 和 `total_turnover`，涨跌幅按上一交易日日线收盘计算 |
| ETF 历史成交额、指数日线和各标的前收 | `rqdatac.get_price(frequency="1d", adjust_type="none")`，取 `total_turnover`、`close` |
| 核心与宽基净申赎、分类表、放量与常态日划分 | `rqdatac_fund.fund.get_daily_units`、`fund.get_nav`、`fund.get_split`，取份额、单位净值与折算事件 |
| 宽基日末规模 | `fund.get_daily_units` 的同日份额 × `fund.get_nav` 的原始单位净值，换算为亿元 |
| 宽基收盘折溢价 | `rqdatac.get_price(frequency="1d", adjust_type="none")` 的 `close` 和 `volume`，以及 `fund.get_nav` 的同日原始单位净值 |
| 交易日及日期窗口 | `rqdatac.get_trading_dates` |
| 成员、上市日期和分类 | 项目配置 `config/universe.json` |

行情使用交易所后缀，例如 `000001.XSHG`。基金 SDK 使用六位基金代码。成交额入应用前为人民币元，图表显示亿元。网页只读历史快照及分时缓存。参见 [净申赎与资金口径](flow-methodology.md)、[数据契约](data-contract.md) 和 [RQData 数据源](data-sources/rqdata.md)。

## 日度资金全景

四个视图使用 `config/universe.json` 的跟踪成员，历史按各自的上市/成立日期确定当日应纳入的成员。时间窗口按自然月或年计算，以当前视图最新数据日期为终点，区间首尾均包含。历史起点随采集配置，下限当前为 2022 年。

- 申赎表和净申赎合计沿用现有拆分与首日规则。表尾按所选区间逐列求和，有缺失值的列留空。累计曲线在区间首个缺口处停止。
- 日末规模是当日范围内各成员规模之和。全部跟踪类别与单类别独立计算，所选范围有缺数则留空。
- 收盘折溢价为 `(不复权收盘价 / 同日原始单位净值 - 1) × 100`，有效成员须有正成交量和有效价格、净值。组合为有效样本等权均值，显示有效数及应纳入数。最新指标日可按基金查看排序。跨境基金若将来纳入配置，需确认价格与净值为同一币种。
- Python 后端的 `/api/board` 在 `broad_metrics` 返回规模和折溢价。序列与 `broad.dates` 对齐，`members` 对应各指标的 `latest`。旧快照可继续打开，新增视图提示更新快照。

## 相关文档

- [HTTP API](http-api.md)
- [数据契约](data-contract.md)
- [净申赎与资金口径](flow-methodology.md)

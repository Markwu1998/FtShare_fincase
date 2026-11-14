# 图表数据接口

| 图表 | RQData 方法与字段 |
|---|---|
| 分时成交额、指数走势、累计成交、20/60 日雷达 | `rqdatac.get_price(frequency="1m", adjust_type="none")`，使用 `total_turnover` 与 `close`，统一 240 点时间轴 |
| ETF 表格、盘中总览 | `rqdatac.current_snapshot` 的 `last` 和 `total_turnover`，涨跌幅按上一交易日日线收盘计算 |
| ETF 历史成交额、指数日线和各标的前收 | `rqdatac.get_price(frequency="1d", adjust_type="none")`，取 `total_turnover`、`close` |
| 核心与宽基净申赎、分类表、放量与常态日划分 | `rqdatac_fund.fund.get_daily_units`、`fund.get_nav`、`fund.get_split`，取份额、单位净值与折算事件 |
| 交易日及日期窗口 | `rqdatac.get_trading_dates` |
| 成员、上市日期和分类 | 项目配置 `config/universe.json` |

行情使用交易所后缀，例如 `000001.XSHG`。基金 SDK 使用六位基金代码。成交额入应用前为人民币元，图表显示亿元。网页只读历史快照及分时缓存。参见 [数据与计算](data.md) 和 [接口迁移对照](market-data-provider-audit.md)。

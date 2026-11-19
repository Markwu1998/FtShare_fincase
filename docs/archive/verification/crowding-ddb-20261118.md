# 市场拥挤度数据源核验：RQData vs DDB

> 状态：历史核验记录 · 核验时间：2026-11-18 · 最后核对：2026-11-19

> 核验时间：2026-11-18 18:00（Asia/Shanghai）  
> 结论先行：**市场拥挤度当前仍固定使用 RQData，不跟随 ETF 资金观测台的日频源选择。DDB 只能支持部分模块，不能做整页 RQData/DDB 切换；公募重仓和融资宏观指标存在硬缺口。**
>
> 本文用于避免重复评估同一问题。若未来要重新启动 DDB 接入，先核对第 7 节的触发条件。

## 1. 当前实现

| 项目 | 当前实现 |
|---|---|
| 成交额集中度 / 大小盘风格 | `etf_backend/crowding.py`，RQData |
| 行业拥挤度 | `etf_backend/crowding_extra.py`，RQData |
| 融资与估值 | `etf_backend/crowding_extra.py`，RQData |
| 公募重仓 | `etf_backend/crowding_extra.py`，RQData 季度持仓 |
| 页面数据源下拉 | **没有** |
| 与 ETF 日频源的关系 | **完全独立**；ETF 选 DDB 不会改变拥挤度来源 |

当前文件：

```text
data/private/rqdata-crowding.json           日度集中度/风格
data/private/rqdata-crowding-industry.json  行业，日度
data/private/rqdata-crowding-financing.json 融资与估值，日度
data/private/rqdata-crowding-funds.json     公募重仓，季度
```

## 2. DDB 数据核验

### 2.1 A 股与指数

| 数据 | DDB 表 | 2026-11-17 实测 | 结论 |
|---|---|---:|---|
| A 股日行情 | `WIND_AShareEODPrices.AShareEODPrices` | 5,586 只 | 可用 |
| A 股市值/换手/涨跌停 | `WIND_AShareEODDerivativeIndicator.AShareEODDerivativeIndicator` | 5,580 只 | 基本可用，少量缺失 |
| 沪深 300 权重/成分 | `WIND_AIndexHS300Weight.AIndexHS300Weight` | 300 条 | 可用 |
| 中证 500 权重/成分 | `WIND_AIndexCSI500Weight.AIndexCSI500Weight` | 500 条 | 可用 |
| 指数收盘 | `WIND_AIndexEODPrices.AIndexEODPrices` | 正常 | 可用 |

字段映射：

| RQData 口径 | DDB 字段 | 说明 |
|---|---|---|
| `total_turnover` | `AShareEODPrices.S_DQ_AMOUNT` | 元 |
| 停牌 | `S_DQ_TRADESTATUS` / `S_DQ_STOPPING` | 需做状态编码映射 |
| 总市值 | `AShareEODDerivativeIndicator.S_VAL_MV` | 万元，需 ×1e4 |
| 流通市值 | `AShareEODDerivativeIndicator.S_DQ_MV` | 万元，需 ×1e4 |
| 指数成分 | `AIndexHS300Weight` / `AIndexCSI500Weight` 当日权重 | 比 `AIndexMembers.CUR_SIGN` 可靠 |
| 指数收盘 | `AIndexEODPrices.S_DQ_CLOSE` | 点 |

**`AIndexMembers` 注意**：2026-11-17 用 `CUR_SIGN=1` 查到沪深300 384 条、中证500 806 条，不能直接当作 300/500 成分表。应使用权重表反推当日成分。

### 2.2 行业分类

| 项目 | DDB | 结论 |
|---|---|---|
| 股票 → 申万行业代码 | `WIND_AShareSWIndustriesClass.AShareSWIndustriesClass` | 有代码，无名称 |
| 当前有效分类 | 约 4,620 只 | 少于 A 股 5,586 只 |
| 行业代码数量 | 约 227 个（含多级） | 需筛一级行业 |
| 行业名称/层级 | 未找到可直接使用的名称表 | **需要额外映射** |

字段：

```text
S_INFO_WINDCODE  股票代码
SW_IND_CODE      申万行业代码
ENTRY_DT         进入日期
REMOVE_DT        移出日期
CUR_SIGN         当前标记
```

缺口：

- RQData 当前使用 `get_instrument_industry(source="sws", level=1)`；
- DDB 只有 `SW_IND_CODE`，没有“电子/医药/银行”等名称；
- 227 个代码包含多级行业，需要静态或外部映射表筛出一级行业；
- 分类覆盖约 4,620 / 5,586，指数成分中可能有未分类股票。

### 2.3 融资与估值

| 指标 | DDB | 结论 |
|---|---|---|
| 融资余额 | `AShareMarginTrade.S_MARGIN_TRADINGBALANCE` | 可用 |
| 融资净增 | 相邻交易日余额差 | 可用 |
| 融资余额/A股市值 | `AShareEODDerivativeIndicator.S_VAL_MV` | 可用 |
| 融资余额/流通市值 | `S_DQ_MV` | 可用 |
| 指数 | `AIndexEODPrices` | 可用 |
| M2 | 未在现有表清单中找到 | **缺** |
| GDP | 未在现有表清单中找到 | **缺** |

2026-11-17 `AShareMarginTrade` 有 2,372 行，属于两融标的范围；按交易所汇总后可以作为融资余额来源，但需要验证与 RQData 的交易所合计口径。

### 2.4 公募重仓

DDB `hermes_etf_data` 表清单：

```text
fund, fund_class, fund_div, fund_enhance_backtest, fund_enhance_sim_stock,
fund_etf_cons, fund_etf_ind, fund_etf_info, fund_etf_nr, fund_etf_prlist,
fund_etf_track, fund_fee, fund_info, fund_nav, fund_stat, mkt_fundd, mkt_fundd_limit
```

其中：

- `fund_etf_cons` 是 **ETF 申赎/成分持仓**；
- 没有**主动公募基金季度前十大持仓**；
- 因此 RQData `fund.get_holdings` 对应的数据在 DDB 中不存在。

**公募重仓模块无法用当前 DDB 完整替代。**

## 3. 模块级可行性矩阵

| 模块 | DDB 数据完整度 | 映射难度 | 结论 |
|---|---|---|---|
| 成交额集中度 / 大小盘风格 | 高 | 中 | 可做，优先验证 |
| 行业拥挤度 | 中 | 中高 | 缺行业名称/层级，覆盖不足 |
| 融资与估值 | 中低 | 中 | 缺 M2/GDP |
| 公募重仓 | 无 | — | **不可行** |

## 4. 因此不建议整页单源

### 整页 `RQData | DDB` 的问题

- DDB 无主动公募持仓；
- DDB 无 M2/GDP；
- 如果页面标成 DDB，公募和融资宏观实际仍来自 RQData，会造成“整页同源”的误解；
- 跨源拼接会让数据源徽标和口径说明复杂。

### 推荐形态

如果未来要做，采用**模块级数据源**：

```text
成交额集中度 / 大小盘风格：RQData | DDB
行业拥挤度：              RQData | DDB
融资与估值：              RQData | DDB（仅部分指标）
公募重仓：                RQData（固定）
```

每个模块显示自己的数据源和频率，而不是页面顶部一个统一开关。

## 5. 当前决策

**不做拥挤度 DDB 切换。**

原因：

1. 公募重仓在 DDB 无数据，无法完整映射；
2. 融资缺 M2/GDP，不能完整替代 RQData；
3. 行业分类缺名称/层级映射，覆盖不足；
4. 当前 RQData 链路已稳定，DDB 收益不足以抵消口径和运维复杂度。

## 6. 复现核验

连接：

```python
import dolphindb as ddb
s = ddb.session()
s.connect("10.72.236.101", 8963, "wuzy", "***")
```

关键表：

```text
WIND_AShareEODPrices.AShareEODPrices
WIND_AShareEODDerivativeIndicator.AShareEODDerivativeIndicator
WIND_AShareSWIndustriesClass.AShareSWIndustriesClass
WIND_AIndexHS300Weight.AIndexHS300Weight
WIND_AIndexCSI500Weight.AIndexCSI500Weight
WIND_AIndexEODPrices.AIndexEODPrices
WIND_AShareMarginTrade.AShareMarginTrade
hermes_etf_data.fund_etf_cons
```

建议核验 SQL：

```sql
-- A 股行情
select count(*) from AShareEODPrices where TRADE_DT=2026.11.17;

-- 沪深300 / 中证500 当日成分
select count(*) from AIndexHS300Weight
where TRADE_DT=2026.11.17 and S_INFO_WINDCODE='000300.SH';
select count(*) from AIndexCSI500Weight
where TRADE_DT=2026.11.17 and S_INFO_WINDCODE='000905.SH';

-- 两融
select count(*) from AShareMarginTrade where TRADE_DT=2026.11.17;

-- 行业分类（CUR_SIGN 为整数 1）
select count(*) from AShareSWIndustriesClass where CUR_SIGN=1;
```

## 7. 重新启动评估的条件

只有满足以下条件，才建议重新评估拥挤度 DDB 切换：

1. DDB 提供主动公募基金季度持仓表，且覆盖当前基金池；
2. DDB 提供可用的 M2/GDP 宏观序列，或明确接受融资模块保留 RQData；
3. 申万一级行业代码 → 名称/层级映射补齐，且指数成分覆盖率达标；
4. 日度集中度用 DDB 与 RQData 做至少 60/250 个交易日逐日对账；
5. 明确模块级数据源展示，而不是整页单源。

在上述条件满足前，**行业拥挤度、融资与估值、公募重仓均继续以 RQData 为准**。

# RQData 与 DDB 日频数据源对照与一致性验证

> 验证日期：2026-11-18  
> 范围：当前 ETF 资金观测台日频链路（20 只核心 ETF + 上证综指 000001.SH）  
> 结论先行：**价格、成交额、成交量、指数、单位净值、折溢价在 DDB 与 RQData 完全一致；DDB 的 ETF 份额/净申赎表 `fund_etf_nr` 停在 2026-07-14，拆分/分红表 `fund_div` 的 UPDATE_TIME 停在 2026-07-16（全表最大事件日 2026-07-20，核心最近事件除权日 2026-07-14），导致 2026-07-14 之后的日度净流入、规模、2026 累计无法由 DDB 独立生成。DDB 目前不能完整替代 RQData，但缺的主要是份额驱动指标，不是行情/净值。**

## 1. 当前监控需要哪些日频数据

| 指标 | 当前实现 | 用途 |
|---|---|---|
| ETF 日收盘价 | RQData `get_price(1d, close)` | 折溢价、涨跌幅、图表 |
| ETF 日成交额 | `total_turnover` | 核心成交额、宽基成交额 |
| ETF 日成交量 | `volume` | 兼容旧口径/校验 |
| 指数日收盘 | `000001.XSHG` 日线 `close` | 指数涨跌、累计图 |
| ETF 日份额 | `fund.get_daily_units` | 净申赎、规模 |
| ETF 单位净值 | `fund.get_nav(unit_net_value)` | 净申赎金额、规模、折溢价 |
| ETF 拆分/折算 | `fund.get_split` | 份额变化还原 |
| 交易日历 | RQData `get_trading_dates` | 对齐、累计 |
| 标的/分类 | `config/universe.json`、`config/basket.json` | 20 只核心 + 362 只宽基 |

## 2. 接口对照与字段映射

### 2.1 标的代码

| 项目 | RQData | DDB |
|---|---|---|
| 上交所 ETF | `510050.XSHG` | `TICKER_SYMBOL=510050` + `EXCHANGE_CD=XSHG` |
| 深交所 ETF | `159915.XSHE` | `TICKER_SYMBOL=159915` + `EXCHANGE_CD=XSHE` |
| 上证综指 | `000001.XSHG` | `S_INFO_WINDCODE=000001.SH` |

### 2.2 字段映射

| 项目指标 | RQData 接口/字段 | DDB 表/字段 | 单位/口径 | 状态 |
|---|---|---|---|---|
| ETF 收盘价 | `get_price(1d, close)` | `hermes_etf_data.mkt_fundd.CLOSE_PRICE` | 元 | ✅ 完全一致 |
| ETF 成交额 | `get_price(1d, total_turnover)` | `mkt_fundd.TURNOVER_VALUE` | 元 | ✅ 完全一致 |
| ETF 成交量 | `get_price(1d, volume)` | `mkt_fundd.TURNOVER_VOL` | 股 | ✅ 完全一致 |
| 指数收盘 | `get_price(1d, 000001.XSHG, close)` | `WIND_AIndexEODPrices.S_DQ_CLOSE` | 点 | ✅ 完全一致 |
| ETF 总份额 | `fund.get_daily_units` | `fund_etf_nr.SHARES` | 份 | ✅ 重叠期一致；⚠️ 2026-07-14 后无数据 |
| ETF 单位净值 | `fund.get_nav(unit_net_value)` | `fund_etf_nr.NAV`；`fund_nav.NAV` | 元/份 | ✅ 完全一致 |
| ETF 拆分比例 | `fund.get_split(split_ratio)` | `fund_etf_nr.SPLIT_RATIO`；`fund_div.SPLIT_RATIO` | 比例 | ✅ 重叠期一致；⚠️ `fund_div` 的 UPDATE_TIME 停在 2026-07-16 |
| 日净申赎 | `(shares_t - shares_{t-1} × split_ratio) × nav_t` | `fund_etf_nr.NET_INFLOW` | 元 | ✅ 完全一致 |
| 日规模 | `shares × nav` | `fund_etf_nr.SCALE` | 元 | ✅ 公式一致 |
| 折溢价 | `close / nav - 1` | `mkt_fundd.CLOSE_PRICE / fund_nav.NAV - 1` | % | ✅ 可计算（无需份额） |
| 交易日历 | `get_trading_dates` | `hermes_md_data.md_trade_cal` | 日期 | ⚠️ 当前连接路径不可用，需确认库名/权限 |
| ETF 产品信息 | `instruments`/`fund.instruments` | `fund_etf_info`、`fund_etf_prlist` | — | ✅ 可用于代码/名称映射 |

### 2.3 DDB 关键表说明

| 表 | 主键/日期 | 关键字段 | 用途 |
|---|---|---|---|
| `hermes_etf_data.mkt_fundd` | `TICKER_SYMBOL + TRADE_DATE` | `CLOSE_PRICE`、`TURNOVER_VOL`、`TURNOVER_VALUE`、`PRE_CLOSE_PRICE` | ETF 日行情 |
| `hermes_etf_data.fund_etf_nr` | `TICKER_SYMBOL + TRADE_DATE` | `SHARES`、`SHARES_CHG`、`SCALE`、`NET_INFLOW`、`NAV`、`SPLIT_RATIO` | ETF 份额/净申赎/规模 |
| `hermes_etf_data.fund_div` | `SECURITY_ID + EFFECT_DATE` | `SPLIT_RATIO`、`DIVIDEND_BF_TAX`、`EXRIGHT_DATE_EXCH` | 拆分/分红事件 |
| `hermes_etf_data.fund_nav` | `SECURITY_ID + END_DATE` | `NAV`、`ACCUM_NAV`、`PUBLISH_DATE` | 基金净值 |
| `hermes_etf_data.fund_etf_prlist` | `SECURITY_ID + TRADE_DATE` | `NAV_PER_CU`、`CREATION_UNIT`、`UPDATE_TIME` | ETF 申赎清单/每篮子净值，**不含总份额** |
| `hermes_etf_data.fund_etf_info` | `SECURITY_ID` | `TICKER_SYMBOL`、`TOTAL_SHARE`、`LIST_DATE` | 静态产品信息；`TOTAL_SHARE` 不是日更份额 |
| `WIND_AIndexEODPrices.AIndexEODPrices` | `S_INFO_WINDCODE + TRADE_DT` | `S_DQ_CLOSE` | 指数日线 |

### 2.4 `fund_div` 字段映射

| DDB 字段 | RQData 对应字段 | 对齐情况 |
|---|---|---|
| `ADJUSTED_TYPE` | `D` → `get_dividend`；`S` → `get_split` | ✅ 类型对齐 |
| `REG_DATE`（分红） | `ex_dividend_date` / `book_closure_date` | ✅ 多数一致；510310 有 1–3 天偏移 |
| `EFFECT_DATE`（分红） | 无直接对应；通常是除权日 T+1 | ❌ 不能直接当 `ex_dividend_date` |
| `EFFECT_DATE`（拆分） | `ex_dividend_date` | ✅ 一致 |
| `EXRIGHT_DATE_EXCH` | 无直接对应；通常是 T+1 | ❌ 不要用它对齐 RQData 除权日 |
| `DIVIDEND_BF_TAX` | `dividend_before_tax` | ✅ 金额一致 |
| `DIVIDEND_AF_TAX` | RQData 未提供税后字段 | — 仅 DDB 有 |
| `ISS_DATE` | `payable_date` | ✅ 如 510500 2026-07-14 均为 2026-07-20 |
| `SPLIT_RATIO` | `split_ratio` | ⚠️ 多数一致；159845 2023-02-28 有 0.39% 差异 |
| `PUBLISH_DATE` | `get_dividend` 未提供公告日 | — 需用其他公告源核对 |

## 3. 完整性评估：DDB 不能独立支撑当前监控

### 3.1 表级新鲜度（2026-11-18 实测）

| 表 | 最大交易日 | 最大 `UPDATE_TIME` | 结论 |
|---|---:|---|---|
| `mkt_fundd` | **2026-11-17** | 2026-11-18 04:00 | 日行情完整 |
| `fund_nav` | **2026-11-17** | 2026-11-18 10:58 | 净值完整 |
| `fund_etf_prlist` | **2026-11-18** | 2026-11-18 09:00 | 申赎清单完整，但无总份额 |
| `fund_div` | **2026-07-20（事件日）** | **2026-07-16 00:11** | 事件表停更；核心最近事件除权日 2026-07-14 |
| `mkt_fundd_limit` | **2026-07-15** | — | 含 `TOTAL_SHARES`，但已停更且 11-17 无核心 ETF 行 |
| `fund_etf_nr` | **2026-07-14** | **2026-07-16 00:29** | **份额/净申赎停更，已落后约 4 个月** |

`fund_etf_nr` 的 20 只核心 ETF **全部**最后日期为 `2026-07-14`，不是单只异常：
```text
159338 2026-07-14    510050 2026-07-14
159352 2026-07-14    510100 2026-07-14
159361 2026-07-14    510300 2026-07-14
159845 2026-07-14    510310 2026-07-14
159915 2026-07-14    510330 2026-07-14
159919 2026-07-14    510500 2026-07-14
159922 2026-07-14    512100 2026-07-14
159949 2026-07-14    512500 2026-07-14
560010 2026-07-14    563360 2026-07-14
588000 2026-07-14    588080 2026-07-14
```

`fund_div` 是**事件表**，不是每日时间序列：

- 字段：`ADJUSTED_TYPE`（D=分红，S=拆分）、`EFFECT_DATE`、`EXRIGHT_DATE_EXCH`、`DIVIDEND_BF_TAX`、`SPLIT_RATIO`；
- 全表 34,207 行，最大事件日 2026-07-20，但最大 `UPDATE_TIME` 为 **2026-07-16 00:11**；
- 20 只核心 ETF 最近一条事件：**510500，RQData 除权日 2026-07-14，DDB `REG_DATE=2026-07-14` / `EFFECT_DATE=2026-07-15`，分红 0.149 元**；
- RQData 核对结果：核心 ETF 在 2026-07-14 之后**没有新的分红/拆分事件**。

因此 `fund_div` 的“最新事件停在 7 月”当前**没有造成核心指标缺失**；但它的 `UPDATE_TIME` 停在 7/16，说明这张表也**停止刷新**了。如果未来发生拆分/分红，DDB 不会自动收录。

### 3.2 替代表排查

为了不依赖停更的 `fund_etf_nr`，逐表检查了 DDB 中所有可能含份额/规模的表：

| 表/字段 | 是否含总份额 | 最新日期 | 结论 |
|---|---|---|---|
| `fund_etf_nr.SHARES` | ✅ | 2026-07-14 | 唯一完整的日更份额源，但已停更 |
| `mkt_fundd_limit.TOTAL_SHARES` | ✅ | 2026-07-15 | 11-17 无核心 ETF 行；且同样停更 |
| `fund_etf_info.TOTAL_SHARE` | ⚠️ 仅静态/上市初始 | 部分 2024–2026 | 不能当日更份额（510050 为 54.35 亿份，实际约 68.52 亿份） |
| `fund_etf_prlist.CREATION_UNIT` | ❌ | 2026-11-18 | 最小申购单位，不是总份额 |
| `fund_etf_prlist.NAV_PER_CU` | ❌ | 2026-11-18 | 每篮子净值，可辅助算单位净值，不能反推总份额 |
| `fund_etf_cons.QUANTITY` | ❌ | — | 每篮子成分数量，不是基金总份额 |
| `fund_nav` | ❌ | 2026-11-17 | 只有净值，没有份额 |
| `mkt_fundd` | ❌ | 2026-11-17 | 只有价格/成交额/成交量，没有总份额或市值 |

进一步尝试的加工路径也不成立：

- `shares = 市值 / NAV`：`mkt_fundd` 没有市值字段，`fund_etf_nr.MARKET_VALUE` 已停更；
- `shares = 成交量 / 换手率`：DDB 没有 ETF 换手率字段；
- 通过 `fund_etf_prlist.CREATION_UNIT` + `fund_etf_cons` 只得到“每篮子”资产，不是全基金份额；
- 通过 `fund_etf_info.TOTAL_SHARE` 加 `NET_INFLOW` 倒推：`NET_INFLOW` 本身也停更，无法闭环。

结论：**当前 DDB 中没有可替代的当前份额表，也无法仅用现有 DDB 字段加工出当前总份额。** 必须修复 `fund_etf_nr` / `mkt_fundd_limit` 的 ETL，或引入外部份额源。

### 3.3 影响

| 当前功能 | 依赖 | DDB 能否独立支持 |
|---|---|---|
| ETF 收盘价、成交额、成交量 | `mkt_fundd` | ✅ 可以 |
| 上证指数 | `AIndexEODPrices` | ✅ 可以 |
| 单位净值 | `fund_nav` / `fund_etf_nr.NAV` | ✅ 可以 |
| 20 只核心 ETF 日度净流入 | `fund_etf_nr.NET_INFLOW` | ❌ 2026-07-14 后无数据 |
| 宽基 2026 累计净流入 | 同上 | ❌ 7 月后断裂 |
| ETF 规模 | `fund_etf_nr.SCALE` | ❌ 同上 |
| 折溢价 | `mkt_fundd.CLOSE_PRICE / fund_nav.NAV` | ✅ 行情和净值都日更，**无需份额** |
| 拆分/分红事件 | `fund_div.SPLIT_RATIO` | ⚠️ 核心最近事件除权日 2026-07-14；表 UPDATE_TIME 停在 7/16，未来事件不会入库 |
| 核心表最新净申赎 | `fund_etf_nr` | ❌ 只能到 2026-07-14 |

补充：

- `fund_etf_prlist.NAV_PER_CU / CREATION_UNIT` 可以近似还原单位净值，但 **不是总份额**，无法替代 `fund_etf_nr`；
- `fund_etf_info.TOTAL_SHARE` 是静态/滞后字段（例如 510050 为 54.35 亿份，而 2026-11-17 实际约 68.52 亿份），不能当日更份额使用；
- 因此 DDB 的“行情 + 净值”完整，但“份额/净申赎”链路不完整。

## 4. 一致性验证

### 4.1 样本与方法

- 核心 20 只 ETF；
- 重叠窗口：2026-07-10 至 2026-07-14（DDB `fund_etf_nr` 最后可得区间）；
- 最新日：2026-11-17（DDB `mkt_fundd` / `fund_nav` 最新）；
- 对每个 `(代码, 交易日)` 比较：
  - `SHARES` vs RQData `fund.get_daily_units`
  - `NAV` vs `fund.get_nav`
  - `NET_INFLOW` vs 项目 flow 公式
  - `CLOSE_PRICE` vs RQData 日线 close
  - `TURNOVER_VALUE` vs `total_turnover`
  - `TURNOVER_VOL` vs `volume`
  - 指数 `S_DQ_CLOSE` vs RQData 000001.XSHG

### 4.2 重叠窗口结果（20 只 × 3 个交易日 = 60 行）

| 指标 | 最大绝对差 | 结论 |
|---|---:|---|
| 份额 `SHARES` | **0** | 完全一致 |
| 单位净值 `NAV` | **0** | 完全一致 |
| 净申赎 `NET_INFLOW` | **9.54e-07 元** | 仅浮点精度 |
| 收盘价 `CLOSE_PRICE` | **0** | 完全一致 |
| 成交额 `TURNOVER_VALUE` | **0.005 元** | 浮点/最小单位 |
| 成交量 `TURNOVER_VOL` | **0** | 完全一致 |
| 指数收盘 | **0** | 完全一致 |

代表性样本：

| 代码 | 日期 | DDB 份额 | RQData 份额 | DDB NAV | RQData NAV | DDB 净流入 | RQData 净流入 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 510050 | 2026-07-14 | 6,852,367,000 | 6,852,367,000 | 3.0531 | 3.0531 | 563,297,000 | 563,297,000 |
| 510300 | 2026-07-14 | 18,481,990,000 | 18,481,990,000 | 4.8429 | 4.8429 | 780,191,200 | 780,191,200 |
| 510330 | 2026-07-14 | 7,749,250,000 | 7,749,250,000 | 5.0484 | 5.0484 | 195,373,100 | 195,373,100 |
| 159915 | 2026-07-14 | 12,469,450,000 | 12,469,450,000 | 3.8710 | 3.8710 | 507,101,000 | 507,101,000 |

拆分事件也一致：

| 代码 | 除权日 | DDB `SPLIT_RATIO` | RQData `split_ratio` |
|---|---:|---:|---:|
| 510310 | 2024-09-20 | 0.499776 | 0.499776 |
| 510100 | 2025-03-07 | 0.497199 | 0.497199 |
| 159922 | 2024-11-29 | 2.500000 | 2.500000 |

### 4.3 `fund_div` 事件一致性

`fund_div` 是事件表，不能按“每天有没有新行”判断，需要按事件匹配：

- 分红 `ADJUSTED_TYPE='D'`：RQData `ex_dividend_date` 对应 DDB **`REG_DATE`**；`EFFECT_DATE` 通常是 T+1；
- 拆分 `ADJUSTED_TYPE='S'`：RQData `ex_dividend_date` 对应 DDB **`EFFECT_DATE`**。

按此口径比较 2021-05-06 之后的 20 只核心 ETF：

| 项目 | 结果 |
|---|---:|
| DDB 事件数 | 47 |
| RQData 事件数 | 47 |
| 完全匹配 | **41** |
| 仅 DDB / 仅 RQData | 6 / 6 |
| 最新事件 | **完全一致**：510500，2026-07-14，分红 0.149 |

6 组不一致**全部是 510310**，且金额相同、日期相差 1–3 天（例如 DDB 2026-01-19 vs RQData 2026-01-20），属于事件日期口径差异，不是金额缺失。

拆分比例有 2 处差异：

| 代码 | 除权日 | DDB `SPLIT_RATIO` | RQData `split_ratio` | 差异 |
|---|---:|---:|---:|---:|
| 510100 | 2023-02-10 | 0.837880 | 0.837886 | 6e-06 |
| 159845 | 2023-02-28 | 0.744752 | 0.747644 | 0.002892（约 0.39%） |

其中 159845 的拆分比例差异需要在正式切换前确认口径；最新事件（2026-07-14 510500 分红）不受影响。

### 4.4 最新日 2026-11-17

20 只核心 ETF 的 **收盘价、成交额、成交量、单位净值** 全部一致：

- 收盘价最大差：**0**
- 成交额最大差：**0**
- 成交量最大差：**0**
- 单位净值最大差：**0**
- 指数：DDB `3584.5794`，RQData 快照 `3584.5794`

但同日 **没有 `fund_etf_nr` 行**，因此净流入/规模无法比较。

## 5. 评估结论与建议

### 5.1 结论

1. **一致性：高。** 在 DDB 有数据的重叠期，DDB 与 RQData 的 ETF 行情、份额、净值、拆分、净申赎、成交额、成交量、指数均一致到浮点/最小单位，基本可视为同一底层数据。
2. **完整性：DDB 不满足当前监控。** `fund_etf_nr` 停在 2026-07-14，导致日度净申赎、规模、2026 累计无法覆盖到 2026-11-17；`fund_div` 的 UPDATE_TIME 停在 2026-07-16（核心最近事件除权日 2026-07-14），若有新拆分/分红事件也需要补源。折溢价仍可由 `mkt_fundd` + `fund_nav` 计算。
3. **因此：DDB 目前只能作为行情/净值的交叉校验源，不能作为当前监控的完整日频主源。**

### 5.2 是否“仅缺份额驱动指标”

就当前面板的日常指标而言，**是的**：

- 行情、成交额、成交量、指数、单位净值、折溢价都可以由 `mkt_fundd` + `fund_nav` + `AIndexEODPrices` 提供；
- 唯一缺失的是 **ETF 总份额**，它直接阻断：
  - 日度净申赎 `NET_INFLOW`
  - 日度规模 `SCALE`
  - 2026 累计净流入
  - 任何以份额为输入的派生指标

严格说，DDB 还缺两张辅助表的最新数据：

- `fund_div` 事件表也停在 2026-07-16（核心最近事件除权日 7/14）：当前核心无新分红/拆分，所以暂不影响；但未来事件会缺；
- `md_trade_cal` 当前路径不可用：可以暂用行情日期或 RQData 日历替代，但严格完整性上是一个待确认项。

因此更准确的说法是：

> **DDB 缺的是“份额及其事件”这一整条链路；当前日常监控里，它表现为净申赎/规模缺失，行情和净值并不缺。**

### 5.3 可选方案

| 方案 | 说明 | 适用 |
|---|---|---|
| A. 修复 DDB ETL | 让 `fund_etf_nr` 恢复每日更新；恢复后 DDB 可完整替代 RQData | 最优 |
| B. DDB 行情 + RQData 份额/净值 | 混合源；因重叠期完全一致，风险较低 | 过渡 |
| C. 继续 RQData 为主 | DDB 仅做独立校验 | 当前最稳 |
| D. 新增 DDB 份额源 | 若另有 Wind 基金份额表，需补充映射和校验 | 需确认 |

### 5.4 对“UI 可选择数据源”的实现建议

- 日频配置建议按能力声明，而不是只写 `provider`：
  ```json
  {
    "daily": "rqdata",
    "realtime": "rqdata",
    "capabilities": {
      "rqdata": ["bars", "turnover", "shares", "nav", "splits", "calendar"],
      "ddb": ["bars", "turnover", "nav", "splits", "index"]
    }
  }
  ```
- DDB 适配器在启动/采集时检查：
  - `fund_etf_nr.max(TRADE_DATE) >= 目标交易日`；
  - 否则直接报 `daily_capability_missing`，不要静默回退到旧份额。
- UI 切换数据源时展示“该源可用指标”，DDB 缺份额时禁用“净流入/规模”，但折溢价/行情/净值可继续展示。
- 实时源继续独立，当前仅 RQData。

## 6. 复现方式

本次验证使用：

- DDB：`hermes_etf_data.mkt_fundd`、`fund_etf_nr`、`fund_nav`、`fund_etf_prlist`、`hermes_md_data.md_trade_cal`；`WIND_AIndexEODPrices`
- RQData：`get_price(1d)`、`fund.get_daily_units`、`fund.get_nav`、`fund.get_split`

校验脚本可按上述 `(代码, 日期)` 对齐后计算差值；建议纳入 `tests/` 或独立的 `scripts/compare_daily_sources.py`，在每次数据源切换前运行。

## 7. 待确认

1. DDB `fund_etf_nr` 停更原因：上游未更新、ETL 故障，还是该表已废弃？
2. 是否有替代的 DDB 基金份额表（Wind `F_UNIT_FLOATSHARE` 等）？
3. `hermes_md_data.md_trade_cal` 当前路径报 `path does not exist`，需要确认正确的库名/权限；
4. 若采用混合源，是否接受“行情来自 DDB、份额/净值来自 RQData”的跨源组合（重叠期已验证一致）。

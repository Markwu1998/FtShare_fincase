# FtShare API → RQData 转换手册

验证日期为 2026-11-14，范围为当前项目的后台行情采集、净申赎计算和历史修正资料。使用同一组本地凭据，行情主样本取 2026-11-12 至 2026-11-13 的沪市 ETF 510300、深市 ETF 159915 和上证综指 000001。

## 免费额度与权限

本次累计使用 27 次有记录的数据调用，11 次成功、16 次返回上游权限拒绝 `403`。MCP 的外层 HTTP 状态为 200，权限拒绝位于结构化响应内。没有观察到 `429`、配额耗尽或可识别的额度响应字段。MCP 工具清单有 256 项，未发现配额或用量查询工具。通过目录发现接口只代表可以构造调用。

用户查到的免费额度不限信息尚未在可公开访问的官方页面独立复核。即使账户页确认免费套餐的总调用次数不限，该规则也仅说明可用接口的总量政策。接口权限、短期限流、分页和单次返回上限仍须按各自规则处理。可按项目接口逐个低频、短区间取样，没有必要探测到服务限流来推算额度。平台套餐及账户政策见 [FtShare 官方文档](https://market.ft.tech/gateway/doc/) 和 [账号中心](https://ftai.chat/me/profile)。

## 运行时接口清单

以下 13 个接口全部完成最小样本探测。范围来自 [盘中服务](../src/server/service.js)、[后台采集](../src/background/collector.js) 和 [历史采集](../scripts/lib/collect.mjs)。

| 项目用途 | FtShare MCP 工具 | 当前 Key | RQData 对应方式 |
|---|---|---|---|
| 沪深交易日历 | `ft_v1_time_trading_calendar` | 成功，两个交易日 | `get_trading_dates`，样本一致 |
| 上证综指历史日线、前收 | `ft_v1_index_candlesticks` | 成功，两条日线 | `get_price(1d)`，样本一致 |
| ETF 历史日线 | `ft_v2_etf_candlesticks_batch` | 403 | `get_price(1d)`，与可用的 FtShare 单只 ETF 接口对账 |
| ETF 历史前收 | `ft_v2_stock_candlesticks_batch` | 403，ETF 批量备用也为 403 | 用上一交易日不复权日线收盘价，保留停牌和特殊除权日验收 |
| ETF 历史分钟 | `ft_v2_etf_minutes_batch` | 403，`ft_v2_etf_minutes` 也为 403 | `get_price(1m)`，已验证 66 个交易日的整篮子历史完整性 |
| ETF 实时分钟 | `ft_v4_stock_realtime_minute_kline`、`ft_v4_etf_realtime_minute_kline` | 均 403 | ETF 分钟推送或日内 tick 聚合，实盘分钟推送待测 |
| 指数实时分钟 | `ft_v4_index_realtime_minute_kline` | 403 | 指数日内 tick 可取小样本，整场更新待测 |
| ETF 实时日 K | `ft_v4_stock_realtime_day_kline`、`ft_v4_etf_realtime_day_kline` | 均 403 | `current_snapshot` 取得最近快照，交易中时效待测 |
| 指数实时日 K | `ft_v4_index_realtime_day_kline` | 403 | `current_snapshot` 取得最近快照，交易中时效待测 |
| ETF 日度份额 | `ft_v2_etf_share` | 403 | `fund.get_daily_units` 可取，深市归属日待校验 |
| ETF 单位净值 | `ft_v2_etf_net_value` | 403 | `fund.get_nav` 可取，最新日期覆盖待观察 |

## 参考数据与历史修正

这些接口出现在 [图表溯源说明](../src/index.html)，历史修正证据随 [成员配置](../config/universe.json) 保存。

| 功能 | FtShare MCP 工具 | 当前 Key | RQData 或其他证据 |
|---|---|---|---|
| ETF 元数据 | `ft_v2_etf_description_all` | 403 | `instruments` 已取两个 ETF 的代码和上市日期，分类、跟踪指数和纳入规则继续核验 |
| 基金基本资料 | `ft_v1_fund_fund_list` | 成功 | 沪深样本名称及上市日与 RQData 一致，上市前基准仍需原始依据 |
| 通用公募基金份额 | `ft_v1_fund_fund_share` | 成功 | 沪深样本均返回日度记录，深市份额有归属差异 |
| 通用公募基金净值 | `ft_v1_fund_fund_net_value` | 成功 | 可对照原始单位净值，保留净值归属日及披露日 |
| ETF 复权因子 | `ft_v1_etf_adjust_factor` | 成功，取得 510300 因子样本 | `fund.get_split` 返回拆分事件，折算比、除权日和分红还需逐项对账 |
| ETF 公告列表、原文 | `ft_v2_announcements_etf_announcements`、`ft_v2_announcements_etf_announcement_download` | 列表 403，原文下载待验证，缺列表提供的有效 `url_hash` | 保留公告证据，用于核对申赎开放日和上市前份额 |
| ETF 盘前净值、PCF 清单及附件 | `ft_v2_etf_pre_single`、`ft_v2_etf_pcf_etf_pcfs`、`ft_v1_etf_pcf_etf_pcf_download` | 盘前与列表 403，附件下载待验证，缺列表提供的有效文件名 | 510300 的 `etf.get_cash_components` 取到 2 条，`etf.get_components` 取到 300 条。原始 PCF 文件、净值归属日和补录证据仍须核验 |

`ft_v1_etf_candlesticks` 可用，已为 510300、159915 取得各两条不复权日线，可作为按需对账样本。其余 MCP 工具超出本项目验证范围。

## 上游模块更新时的转换步骤

1. 从新模块的调用处记录 FtShare 工具、参数、字段、单位、更新时间和权限，先确认图表实际依赖。
2. 按上表选 RQData 数据。行情符号 .SH/.SZ 映射到 .XSHG/.XSHE，基金 SDK 使用六位代码。日线与分钟使用不复权。分钟 total_turnover 是区间金额，快照同名字段是日累计。上证综指为 000001.XSHG。
3. 对账时使用沪深各一只 ETF 与指数。检查 09:30/09:31、午休、收盘及整日分钟合计。RQData 历史与盘中统一使用 240 点。20/60 日雷达须以同口径分钟基准计算。
4. 份额按各源归属日对齐。两只沪市 ETF 各 18 条同日记录相等，两只深市 ETF 各 18 条 RQData 记录与 FtShare 下一交易日标签相等。RQData 现直接使用其日期，勿再套一次 FtShare 深市移位。折算使用 fund.get_split 并与 NAV 因子校验。上市前份额、公告正文和 PCF 原件需要独立证据。
5. 将新的取数写入采集桥，保留快照字段的单位和 null 语义，更新图表数据详情。先用历史样本回放，再在真实交易时段验收延迟和重连。

## 当前覆盖

本地 RQData 已接入日线、历史分钟、盘中分钟轮询、最新价与日累计成交额、交易日历、日度份额、单位净值与基金折算。成员定义来自项目配置。PCF、交易状态、元数据候选接口用于比对；公告原文下载和上市前基准无直接等价路径。最新快照的上市首日缺失及发布延迟保留为缺口。

在 2026-11-14 的回放中已验证 66 个完整分钟交易日、750 个核心净申赎交易日及 1,061 个完整宽基资金日。当前 RQData 盘中模式的实盘延迟与重连仍待交易时段验收。

参考 [RQData 行情](https://www.ricequant.com/doc/rqdata/python/generic-api)、[RQData 基金](https://www.ricequant.com/doc/rqdata/python/fund-mod.html) 和 [FtShare 接口](https://market.ft.tech/gateway/doc/)。

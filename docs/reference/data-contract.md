# 数据契约

> 状态：现行 · 适用版本：`schema: 1` · 最后核对：2026-11-19

历史快照通过后台获取不复权日线、1 分钟行情、交易日历、ETF 日度份额、单位净值和折算记录。计算口径见 [净申赎与资金口径](flow-methodology.md)。

## 快照结构

结构版本为 `schema: 1`。示例可查看 `data/demo-seed.json`，其中数值为程序生成的演示样本。

| 字段 | 内容 |
|---|---|
| `formula` / `flow_policy` | 与本版模型一致的计算版本 |
| `universe` / `categories` / `calendar` | 成员、12 分类与交易日历 |
| `history` | 核心 20 只的日期、成交额、指数、净申购与覆盖数 |
| `broad` | 分类日度资金、每日总额、覆盖数和问题明细 |
| `broad_metrics` | 各分类的日末规模与收盘折溢价、有效成员数、最新日期与明细 |
| `core_fund_daily` | 最新归属日的 20 只核心 ETF 逐基金日度记录，供雷达日度信号使用 |
| `live_snapshot` | 可选的上次成功行情，作为共享缓存不可用时的兜底 |
| `latest_flow_date` / `built_at` | 最新资金归属日及快照生成时间 |
| `source` | 日频源元数据（`id`、`capabilities`、`readiness` 等） |

`core_fund_daily` 示意（金额单位为亿元）：

```json
{
  "date": "2026-11-11",
  "captured_at": "2026-11-12T08:00:00Z",
  "rows": [{"symbol": "510300.SH", "flow": 1.25}]
}
```

## 校验规则

`etf_backend/web.py` 的 `validate_seed` 与 Node 遗留的 `scripts/lib/seed.mjs` 都会校验：

- 日期顺序；
- 数组长度；
- 成员与分类；
- 分类合计；
- 覆盖完整性；
- 分钟累计。

演示与真实模式的浏览器数据缓存分别保存，避免换模式后混用。

## 增量合并

- `history --recent` 只重查最近 14 天重叠窗口，按日期合并进现有快照。
- 旧日期原样保留，窗口内用新值替换（含上游修订）。
- 合并后执行防倒退校验；失败不覆盖文件。
- 成员、公式、分钟轴不一致时直接失败。

## 雷达数据要求

构建时设置 `ENABLE_RADAR=1`。`minutes` 提供观察日之前的完整 20／60 日样本，`/api/live` 提供当前 20 只篮子的分钟成交与覆盖数。短时比较使用相同 5 分钟窗口，不跨午休。

正式日度发布要求 20 只记录齐全，日期等于 `latest_flow_date`，合计与 `history.netflow` 对应日期一致。直接导入的旧快照未带 `core_fund_daily` 时，页面显示日度数据待齐，需在后台补全后归档。

## 相关文档

- [净申赎与资金口径](flow-methodology.md)
- [ETF 异动雷达](radar.md)
- [HTTP API](http-api.md)
- [数据源切换](data-sources/switching.md)

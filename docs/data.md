# RQData 数据接入与资金计算

历史快照通过后台获取不复权日线、1 分钟行情、交易日历、ETF 日度份额、单位净值和折算记录。采集及回放步骤见 [本地操作](rqdata-market.md)，字段见 [图表接口](chart-interfaces.md)。

同一归属日净申赎使用当日份额减去经折算校正的上一交易日份额，再乘原始单位净值。上市前不纳入。上市首日前值、净值或折算证据不足时保留缺口，区间累计从起始日计算并在首个缺口停止。

## 历史校正记录

使用已核实的上市基准、份额折算或净值记录校正历史；核实依据随记录保留。

`data/private/flow-recoveries.json`：

```json
{"funds": {}}
```

按基金代码填写经过核实的 `listing_baseline`、`splits` 或 `navs`；字段约束见 `src/server/model.js` 的 `fundDaily`，具体分支在测试中有合成样本。事件资料应保留公告来源、日期和核验依据。已有授权快照中携带的 `flow_evidence` 会随快照一起导入，不需要重新填写。

## 快照结构

结构版本为 `schema: 1`，示例可查看 `data/demo-seed.json` 的组织方式，但其中数值为程序生成的演示样本。

| 字段 | 内容 |
|---|---|
| `formula` / `flow_policy` | 与本版模型一致的计算版本 |
| `universe` / `categories` / `calendar` | 成员、12 分类与交易日历 |
| `history` | 核心 20 只的日期、成交额、指数、净申购与覆盖数 |
| `broad` | 分类日度资金、每日总额、覆盖数和问题明细 |
| `core_fund_daily` | 最新归属日的20只核心ETF逐基金日度记录，供雷达日度信号使用 |
| `live_snapshot` | 可选的上次成功行情，作为共享缓存不可用时的兜底 |
| `latest_flow_date` / `built_at` | 最新资金归属日及快照生成时间 |

`scripts/lib/seed.mjs` 负责校验：日期顺序、数组长度、成员、分类合计、覆盖完整性和分钟累计。演示与真实模式的浏览器数据缓存分别保存，避免换模式后混用。

## 雷达数据要求

构建时设置`ENABLE_RADAR=1`。`minutes`提供观察日之前的完整20／60日样本，`/api/live`提供当前20只篮子的分钟成交与覆盖数。短时比较使用相同5分钟窗口，不跨午休。

`core_fund_daily`示意结构如下，金额单位为亿元；示意未列出20行：

```json
{
  "date": "2026-11-11",
  "captured_at": "2026-11-12T08:00:00Z",
  "rows": [{"symbol": "510300.SH", "flow": 1.25}]
}
```

正式日度发布要求20只记录齐全，日期等于`latest_flow_date`，合计与`history.netflow`对应日期一致；采集器复用已计算的逐基金结果，不另发请求。直接导入的旧快照未带此字段时，页面显示日度数据待齐，需在后台补全后归档。雷达发布门槛见`scripts/lib/cloud-publication.mjs`。

分钟成交与日度净申赎分别判断。日度记录按归属日展示，不因成交放量而推断盘中申购或交易主体。[规则与阈值](radar-preview.md) · [窗口回放](radar-window-review.md)

# RQData 数据接入与资金计算

历史快照通过后台获取不复权日线、1 分钟行情、交易日历、ETF 日度份额、单位净值和折算记录。采集与增量更新见 [Python 后端](python-backend.md)，接口字段见 [图表接口](chart-interfaces.md)，运行与定时任务见 [运维手册](operations.md)。

核心篮子的唯一来源是 `config/basket.json`；`scripts/basket.py` 负责校验（恰好 20 只、不重复、成员在 `config/universe.json` 中）并同步到 Python/Node 常量与 `is_core_basket` 标记。

同一归属日净申赎使用当日份额减去经折算校正的上一交易日份额，再乘原始单位净值。上市前不纳入。上市首日前值、净值或折算证据不足时保留缺口，区间累计从起始日计算并在首个缺口停止。

## 历史校正记录

使用已核实的上市基准、份额折算或净值记录校正历史；核实依据随记录保留。

`data/private/flow-recoveries.json`：

```json
{"funds": {}}
```

按基金代码填写经过核实的 `listing_baseline`、`splits` 或 `navs`；字段约束见 `etf_backend/model.py` 的 `fund_flows`，具体分支在测试中有合成样本。事件资料应保留公告来源、日期和核验依据。已有授权快照中携带的 `flow_evidence` 会随快照一起导入，不需要重新填写。

## 快照结构

结构版本为 `schema: 1`，示例可查看 `data/demo-seed.json` 的组织方式，但其中数值为程序生成的演示样本。

| 字段 | 内容 |
|---|---|
| `formula` / `flow_policy` | 与本版模型一致的计算版本 |
| `universe` / `categories` / `calendar` | 成员、12 分类与交易日历 |
| `history` | 核心 20 只的日期、成交额、指数、净申购与覆盖数 |
| `broad` | 分类日度资金、每日总额、覆盖数和问题明细 |
| `broad_metrics` | 各分类的日末规模与收盘折溢价、有效成员数，以及最新日期与明细 |
| `core_fund_daily` | 最新归属日的20只核心ETF逐基金日度记录，供雷达日度信号使用 |
| `live_snapshot` | 可选的上次成功行情，作为共享缓存不可用时的兜底 |
| `latest_flow_date` / `built_at` | 最新资金归属日及快照生成时间 |

`etf_backend/web.py` 的 `validate_seed` 与 Node 遗留的 `scripts/lib/seed.mjs` 都会校验：日期顺序、数组长度、成员、分类合计、覆盖完整性和分钟累计。演示与真实模式的浏览器数据缓存分别保存，避免换模式后混用。

## 增量更新与收盘补全

- 日频快照：`history --recent` 只重查最近 14 天重叠窗口，按日期合并进现有快照；旧日期原样保留，窗口内用新值替换（含上游修订）。合并后仍会执行防倒退校验，失败不覆盖文件。
- 覆盖口径：新上市基金在首个交易日没有可归属的份额变化，不纳入当日分类覆盖（flow 留空但不阻断分类合计）；`delisted_date` 之后的基金不再纳入覆盖，避免退市标的永久阻塞宽基合计。
- 盘中缓存：采集器 `live --watch` 按分钟写入；收盘后可用 `live --close` 用当日 1m 线重建完整快照（不带 replay 标记），保证 15:00 不缺分钟。
- 网页按文件 `(inode, size, mtime)` 热加载，数据更新不需要重启 FastAPI。
- 体量参考：362 只 ETF、2025 年起的快照约 1.0 MB，每个交易日新增约 1.1 KB；66 天分钟样本约 0.43 MB。

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

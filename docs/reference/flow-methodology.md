# 净申赎与资金口径

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

本文只描述计算口径。接口字段见 [数据契约](../reference/data-contract.md)，图表与数据源映射见 [图表数据接口](../reference/chart-api.md)。

## 核心篮子

核心 20 只 ETF 的唯一来源是 `config/basket.json`。`scripts/basket.py` 负责校验（恰好 20 只、不重复、成员在 `config/universe.json` 中）并同步到 Python/Node 常量与 `is_core_basket` 标记。

篮子变更会改变日度净申赎历史，必须全量重建，不能使用 `--recent`。

## 净申赎

同一归属日净申赎使用：

```text
flow_t = (shares_t - shares_{t-1} × split_ratio_t) × nav_t
```

- `shares`：当日总份额（RQData `fund.get_daily_units`）。
- `nav`：原始单位净值（`fund.get_nav` 的 `unit_net_value`）。
- `split_ratio`：份额折算比例（`fund.get_split`）。
- 上市前不纳入。
- 上市首日前值、净值或折算证据不足时保留缺口。
- 区间累计从起始日计算，并在首个缺口处停止。

不使用成交额或涨跌幅推断净申赎，也不从盘中成交放量推断申购或交易主体。

## 规模与折溢价

- 日末规模 = 当日份额 × 同日原始单位净值，换算为亿元。
- 收盘折溢价 = `(不复权收盘价 / 同日原始单位净值 - 1) × 100`。
- 有效成员须有正成交量和有效价格、净值。
- 组合为有效样本等权均值，同时显示有效数与应纳入数。
- 跨境基金若将来纳入配置，需确认价格与净值为同一币种。

## 覆盖口径

- 新上市基金在首个交易日没有可归属的份额变化，不纳入当日分类覆盖（flow 留空但不阻断分类合计）。
- `delisted_date` 之后的基金不再纳入覆盖，避免退市标的永久阻塞宽基合计。
- 覆盖不足时快照校验失败，不发布不完整结果。

## 历史校正

使用已核实的上市基准、份额折算或净值记录校正历史；核实依据随记录保留。

`data/private/flow-recoveries.json`：

```json
{"funds": {}}
```

按基金代码填写经过核实的 `listing_baseline`、`splits` 或 `navs`；字段约束见 `etf_backend/model.py` 的 `fund_flows`。事件资料应保留公告来源、日期和核验依据。已有授权快照中携带的 `flow_evidence` 会随快照一起导入，不需要重新填写。

## 偏差控制清单

| 风险 | 控制方式 |
|---|---|
| 前视偏差 | 雷达基准只使用观察日之前的完整样本，不引入当日或未来日 |
| 复权口径 | 价格统一使用 `adjust_type="none"` |
| 披露延迟 | 日频更新在盘前/午间执行，最新归属日随披露推进 |
| 生存偏差 | 退市后剔除；上市前不纳入 |
| 折算事件 | 份额变化先经 `split_ratio` 校正 |
| 缺失值 | 保留缺口，不从成交额或其他字段补齐 |

## 相关文档

- [数据契约](../reference/data-contract.md)
- [ETF 异动雷达](../reference/radar.md)
- [市场拥挤度](../reference/crowding.md)

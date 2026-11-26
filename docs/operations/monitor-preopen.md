# Aces 监控日初准备

> 状态：独立生成脚本已实现，Aces / Qt 自动加载仍在接入阶段。2026-11-26。

监控日初文件使用独立的 `monitor-preopen.json`，不写 IndexArb 的 `daily_factors.json`，不修改 `daily_factor_file`。日初准备进程离线查询 DDB；生产实时输入继续复用 MatsFileGateway 的 SQLite 行情及 `EVENT_TICK`，IOPV 由 IndexArb PCF 计算。

## 配置与生成

配置模板为 [`config/monitor-preopen.json`](../../config/monitor-preopen.json)，入口为 [`scripts/prepare-monitor-preopen.py`](../../scripts/prepare-monitor-preopen.py)。建议将模板复制为 `.local/monitor-preopen.site.json`，按生产机器调整路径。

| 配置键 | 内容 |
|---|---|
| `basket_file` | 监控 ETF 篮子，沿用 `basket.json` 的 `symbols`。 |
| `universe_file` | ETF 名称、跟踪指数、分类与上市日期。 |
| `burst_config_file` | 沿用现有异动配置的 `symbols`（非空时覆盖篮子）、`amount_window_quantile`、`futures_evidence`。 |
| `output` | 独立准备文件路径；`{date}` 为 `YYYYMMDD`，`{day}` 为 `YYYY-MM-DD`。默认按交易日保存。 |
| `prepare_tick_references` | 是否计算 5／30／60 秒候选基准，默认开启。关闭只省计算与输出，分钟基准仍需历史 Tick。 |
| `archives` | 现有 ETF 日频及四份市场归档路径。生成时记录存在状态、供应商、日期和 SHA256，不重算这些历史图表。 |

所有相对路径以本仓库根目录为基准，包括站点配置中的路径；也支持绝对路径。生产可将输出与归档统一放在共享数据目录。配置中不填写密码。

Windows 在项目目录执行，启动脚本沿用 `.env` 中的 `DDB_HOST`、`DDB_PORT`、`DDB_USER`、`DDB_PASSWORD`，不会读取 RQData 许可：

```powershell
Set-Location D:\ETFMonitor\FtShare_fincase
& .\scripts\python-backend.ps1 -Mode monitor-preopen -Arguments @('--day=2026-11-26')

# 指定独立站点配置
& .\scripts\python-backend.ps1 -Mode monitor-preopen -Arguments @('--day=2026-11-26','--config=.local/monitor-preopen.site.json')

# 离线检查已有文件的交易日、标的、分位数、规则及基准结构
& .\scripts\python-backend.ps1 -Mode monitor-preopen -Arguments @('--day=2026-11-26','--check')
```

Windows 或 Linux 也可直接运行已配置 DDB 环境变量的 Python：

```text
python scripts/prepare-monitor-preopen.py --day 2026-11-26 --config config/monitor-preopen.json
python scripts/prepare-monitor-preopen.py --day 2026-11-26 --check
```

支持 `--output <完整文件路径>` 临时覆盖输出。需要当前项目的 Python 依赖、`numpy`、`pandas` 和可选的 `dolphindb` SDK；沿用已有 DDB 环境即可。默认输出 `data/private/monitor-preopen/20261126/monitor-preopen.json`，位于 Git 忽略目录。

`--day` 必须是操作方确定的目标交易日。脚本从目标日前 240 个自然日的上证指数日行情中选择最后 60 个已观察交易日，完全不查询目标日 Tick，也不要求目标日已经产生数据。文件标记 `target_calendar_check="caller_supplied"`；它没有以历史行情推断未来节假日。`--check` 是离线身份和结构检查，不会在线确认 DDB 是否修订或新增了交易日。

## 独立文件内容与口径

| 字段 | 内容及读取方式 |
|---|---|
| `day` / `as_of` / `calendar` | 目标日、历史最后交易日、严格早于目标日的 60 日历。 |
| `symbols` / `instruments` | 实际标的范围及指数映射，保留 SH／SZ 交易所后缀。 |
| `parameters` | 分位数、Aces 默认 `minute_driver="tick"` 和期货证据开关。用户后续可在界面切换 Tick／Bar；切换会话机制见开发方案。 |
| `minute_baseline` | 兼容原基准结构的 `identity`、逐 ETF `baseline`、`index` 和 `ready`。逐 ETF 含 20／60 日**日成交额中位数**，以及 1／2／5／10 分钟同窗 P75（或配置分位数）及 P50。 |
| `tick_baseline` | 原 `tick-persistence-v1` 格式，包含 `enabled`、交易日、分位数及每 ETF 的 5／30／60 秒参考值；可交给现有 `load_references()`。 |
| `previous_daily` | T-1 收盘价、成交量、成交额、份额、单位净值及 DDB `NET_INFLOW`。只取历史最后交易日；未查询当日净申赎。 |
| `coverage` | 逐 ETF 的日样本数、有效分钟窗口数、秒级参考时点数及前日缺失字段。 |
| `archives` | 原有图表历史数据的文件清单与原始供应商、日期、哈希。市场归档可能仍是 RQData，不标成 DDB。 |
| `sources` | 使用的 DDB 表、单位换算、实时与 IOPV 归属。输出不包含连接密码。 |

数据读取沿用现有 DDB 表与换算：

- `WIND_AIndexEODPrices.AIndexEODPrices.TRADE_DT`：历史交易日。
- `hermes_etf_data.mkt_fundd.TURNOVER_VALUE`：日成交额，单位元；`CLOSE_PRICE`、`TURNOVER_VOL`：前日价格与成交量。
- `hermes_etf_data.fund_etf_nr`：T-1 `SHARES`、`NAV`、`NET_INFLOW`，不以旧日数据填补缺项。
- `MD.MDSnapshot`：ETF 历史累计成交额 `TotalValueTrade` 和指数 `LastPx`，均沿用现有适配器除以 10000；日期取 `MDDate`，时刻取 `MDTime`，不能使用时间字段中的占位日期。

分钟累计成交额差分完全复用 `ddb_replay.minute_amounts_from_seconds()`，包含开盘集合竞价、午休锚点、5 秒收盘宽限和 10 秒陈旧值限制。正式检测器及累计成交额算法不变。秒级准备复用 `tick_factors` 的同一差分和分位算法；按天先压缩成五秒数组，再合并为 60 日矩阵，保留缺口为 NaN。日基准和同窗分位的最低样本仍为 20 日至少 19 个、60 日至少 55 个。

指数辅助证据沿用两分钟收益 P95、至少 55 日样本。DDB 中没有对应指数 Tick 时 `index` 无该指数参考值，`index_reference_codes` 列出实际覆盖，不伪造指数抬升信号。期货实际挂牌合约、Tick 和持仓量由 Aces 行情／合约事件提供；脚本不根据昨日的四期限合约推定今日合约。

## 就绪、缺数与接入

`coverage.<ETF>.formal_ready` 要求该 ETF 两个日中位数及所有可交易分钟窗口的正式分位值均有效且大于零。`minute_baseline.ready` 表示至少一只 ETF 达标，消费者必须逐只检查，不能据此认为所有 ETF 就绪。

`tick_available` 表示 5／30／60 秒三类参考均有可用时点，`tick_complete` 表示所有结构允许的参考时点都齐备；实际以 `tick_slots` 为准。开盘或午后开市锚点缺少足够新鲜的前序 Tick 时，个别时点正常缺失。盘中沿用现有规则，仅在当时所需参考完整时产生候选。缺少秒级基准不替代为分钟基准，也不改变分钟正式信号。

前日缺项通过 `missing_previous_fields` 展示；资金与净值字段的缺失不改变成交额异动门槛。金额为零和字段为空分别保留。归档状态为 `missing`、`invalid`、`unknown_date`、`after_cutoff`、`current` 或 `older`，只是文件清点结果；季度基金持仓的 `older` 不代表过期。披露日在历史截止日之后也标为 `after_cutoff`。历史研究需要固定当时可见的归档版本，清单哈希本身不能消除事后修订。

退出码 `0` 表示所有选中 ETF 的正式分钟基准完整；`2` 表示生成并保存了部分可用的准备包，需检查覆盖后决定启用哪些标的。日频参考、秒级和指数覆盖另外查看上述字段，退出码 0 不代表所有面板数据完整。连接、数据异常、重复 Tick、累计额回退或无效值会报错退出；完整准备完成前不替换旧文件。写入沿用同目录临时文件加原子替换，不会写出半份 JSON。

未来 Aces 读取入口为 `etf_backend.preopen.load_preopen(path, day, as_of, symbols, q)`，应传入应用已知的交易日与前一交易日，并按每项覆盖启用功能。分钟块可交给现有 `BurstDay` 还原，秒级块保留原加载格式。此轮没有将新文件接入 Aces 引擎或原 RQData `PushSession`；两者实际启动流程保持现状。

完整 Web 图表仍读取原 ETF／市场归档，准备文件不复制全量多年历史。可以先运行现有 DDB 日频更新，再生成本文件：

```powershell
& .\scripts\python-backend.ps1 -Mode history -Arguments @('--source=ddb','--seed=data/private/ddb-seed.json')
& .\scripts\python-backend.ps1 -Mode monitor-preopen -Arguments @('--day=2026-11-26')
```

## 运行安排与验证

不创建或注册本机计划任务。生产调度可在 DDB 的 T-1 数据更新后、开盘前运行上述命令，具体时刻以目标机器的 ETL 和交易日历为准。首次运行会读取 60 天历史 Tick，耗时需在生产库实测；按天串行读取限制峰值原始数据量，但仍属于历史批处理。准备过程在 Aces 启动前或独立进程中执行，不放在 Qt 主线程或行情事件回调中。

分位参数修改后重新生成相应目标日文件，不能复用旧分位数／旧规则的准备包。历史回放指定对应日期，继续使用严格此前的数据，文件按日隔离。

离线专项测试：

```text
python -m unittest discover -s tests -p test_monitor_preopen.py -v
```

测试用合成 DDB 响应验证日前截止、金额单位、日中位数、P75／P50、分钟与秒级基准、指数辅助证据、缺口、重复记录、原子输出及离线检查。合成结果不作为生产 DDB 的权限、完整性或耗时验收。

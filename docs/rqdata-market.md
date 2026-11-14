# 本地接入 RQData 行情

新部署使用 [Python 后端](python-backend.md)。本页的 Node 启动命令用于既有部署和回退。

Node 后台采集器可用 RQData 提供 ETF 和上证综指的日线、历史分钟、交易日历和当日行情。RQData 分钟轴为北京时间 09:31–11:30、13:01–15:00。发布历史与当日快照使用同一时间轴，不同分钟口径的快照隔离。所有成交额由 Python 以人民币元返回，项目聚合时转换为亿元。

## 配置与启动

创建本地 `.local/rqdata-market.json`，内容为 `{"provider":"RQData","minute_days":66,"fund_history":true}`。该文件也可覆盖 `config/collection.json` 的日期范围。行情需要 `rqdatac`，份额和净值回补还需要 `rqdatac-fund`。Windows 启动脚本可从当前 Windows 用户加密的 `RQDATA_CREDENTIAL_FILE` 加载配置；已在 SDK 配置凭据的环境可直接运行 Node 采集入口。

在 PowerShell 中设置本地参数后运行以下命令。

```powershell
$env:MARKET_DATA_CONFIG_FILE = '.local/rqdata-market.json'
$env:FTSHARE_SEED_FILE = 'data/private/rqdata-seed.json'
$env:FTSHARE_LIVE_CACHE_FILE = 'data/private/rqdata-live.json'
$env:DATA_MODE = 'live'
$env:ENABLE_RADAR = '1'
& ./scripts/rqdata.ps1 history
npm.cmd run build
npm.cmd start
```

在另一终端设置相同的配置和缓存路径，并运行 `& ./scripts/rqdata.ps1 live --watch`。历史采集和构建可按日执行。普通 Node 环境可使用 `node --env-file-if-exists=.env scripts/collect.mjs` 和 `node --env-file-if-exists=.env scripts/collect-live.mjs --watch`。Cloudflare 只读部署使用外部采集器发布的 RQData 缓存。

这些配置也可写入本地 `.env`，之后在项目目录直接执行 `npm.cmd run build` 和 `npm.cmd start`。快照与缓存的环境变量保留兼容名称，其值是本地文件路径。

手动回放时，在采集终端设置 `RQDATA_REPLAY_CACHE_FILE=data/private/rqdata-replay.json`，运行 `& ./scripts/rqdata.ps1 live --replay 2026-11-13`。验证服务需以该文件作为 `FTSHARE_LIVE_CACHE_FILE` 并显式设置 `RQDATA_ALLOW_REPLAY=1`。回放标记会保留在响应中。若要验证 60 日雷达，历史快照需要覆盖回放日前至少 60 个完整交易日。

RQData 份额按其归属日读取。跨源归属日验证见 [迁移手册](market-data-provider-audit.md)。基金折算记录须经相邻净值因子验证。上市前份额没有依据时保留缺口，累计曲线在缺口处停止。兼容的旧资金快照可以保留既有完整记录。

## 参考资料对应关系

| 看板用途 | RQData 对应数据与验证状态 |
|---|---|
| ETF 批量日线、历史分钟、前收、指数日线 | `get_price` 支持批量不复权查询。上一交易日收盘用于前收，盘中成交额取 `current_snapshot`。 |
| 指数与 ETF 盘中分钟 | 用当日 `get_price(1m)` 轮询，快照提供最新价和日累计成交额。历史路径与回放已验证，开市时段的实时权限和延迟待验收。 |
| ETF 份额与单位净值 | `fund.get_daily_units`、`fund.get_nav` 已批量接入。回补只采用已返回的净值和份额，最新归属日随披露进度更新。 |
| ETF 基础信息、折算 | `instruments`、`fund.instruments`、`fund.get_split`。上市日和拆分记录已有样本，跟踪指数、份额基准及折算生效日需逐项核对。 |
| PCF 现金差额、成分证券及净值 | `etf.get_cash_components`、`etf.get_components` 已取样。按申赎单位净值换算前须核对份额单位和 `pre_date`。原始 PCF XML 的取回能力未验证。 |
| 基金公告原文、申赎开放日证据 | 现有校正保留原公告及 PCF 证据。尚未确认 RQData 有可对应的公告原文下载接口。 |

取样、权限和差异详见 [行情源评估](market-data-provider-audit.md)。接口见 [米筐行情文档](https://www.ricequant.com/doc/rqdata/python/generic-api) 和 [基金文档](https://www.ricequant.com/doc/rqdata/python/fund-mod.html)。

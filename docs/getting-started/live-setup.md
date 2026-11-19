# 实盘数据接入

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

实盘模式需要具备相应权限的 RQData 账号，以及安装了 `rqdatac`、`rqdatac-fund` 的 Python 环境。网页只读取采集器发布的结果，不直接访问 RQData。

## 1. 配置凭据

凭据来自用户级环境变量 `RQDATAC2_CONF`，或由 SDK 的本地加密配置提供。Windows 启动脚本也可从 `RQDATA_CREDENTIAL_FILE` 加载。

采集器可使用本地配置文件（默认 `.local/rqdata-market.json`）：

```json
{"provider": "RQData", "minute_days": 66, "fund_history": true}
```

该文件也可覆盖 `config/collection.json` 的日期范围，详见 [配置参考](configuration.md)。

## 2. 首次全量历史

```powershell
$env:DATA_MODE = 'live'
& .\scripts\python-backend.ps1 history
```

首次约数分钟。已有 `data/private/rqdata-seed.json` 时跳过；之后每日只用 `--recent`。覆盖率不足或披露未到会直接失败，不覆盖旧快照。

Linux 或已配置 SDK 凭据的环境：

```sh
python -m etf_backend.collect history --config .local/rqdata-market.json
```

## 3. 启动网页

```powershell
$env:PORT = '8080'
& .\scripts\python-backend.ps1 web
```

```sh
DATA_MODE=live ENABLE_RADAR=1 uvicorn etf_backend.web:app --host 127.0.0.1 --port 8080
```

普通 `web` 模式下，分时图、实时排行和成交雷达要等盘中采集器写入首份缓存；日度资金和历史图表读取历史快照。

## 4. 启动盘中采集器

另开一个终端：

```powershell
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--watch')
```

- 09:15 预载 60 日分钟基准，09:30 订阅分钟线与 tick。
- 11:30–13:00 保持同一订阅不断流，13:01 不需要额外回补。
- 开市中启动、进程重启或上游断流时，用 RQData 分钟线补齐已结束分钟。
- 收盘后可执行一次 `live --close` 补全到 15:00。

详见 [数据管线](../operations/data-pipeline.md)。

## 5. 回放

```powershell
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--replay=2026-11-13','--cache=.local\python-rqdata-replay.json')
& .\scripts\python-backend.ps1 web -ReplayCache '.local\python-rqdata-replay.json'
```

回放缓存需同时设为网页的 `FTSHARE_LIVE_CACHE_FILE`，并设置 `RQDATA_ALLOW_REPLAY=1`。盘中模式应使用独立的缓存文件。

## 相关文档

- [配置参考](configuration.md)
- [数据管线](../operations/data-pipeline.md)
- [运维手册](../operations/runbook.md)

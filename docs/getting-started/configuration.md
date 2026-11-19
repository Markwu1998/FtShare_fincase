# 配置参考

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

`.env` 保存运行参数与路径；`config/*.json` 保存业务配置。`.env` 不入库，提交前用 `git status` 核对。

## `.env` 变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATA_MODE` | `demo` | `demo` 使用样本数据，`live` 使用授权快照 |
| `ENABLE_RADAR` | `0` | `1` 启用 ETF 异动雷达 |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | 网页监听地址与端口 |
| `FTSHARE_SEED_FILE` | `data/private/rqdata-seed.json` | 历史快照 |
| `FTSHARE_LIVE_CACHE_FILE` | `data/private/rqdata-live.json` | 盘中分钟缓存 |
| `CROWDING_FILE` | `data/private/rqdata-crowding.json` | 拥挤度日度快照 |
| `ETF_BURST_DIR` | `data/private/etf-burst` | 逐 ETF 放量实时与历史归档 |
| `ETF_BURST_CONFIG` | `config/etf-burst.json` | 放量参数 |
| `FTSHARE_SOURCE_CONFIG` | `config/data-sources.json` | 日频数据源注册 |
| `RQDATA_PYTHON` | `python` | 启动脚本使用的解释器 |
| `RQDATA_CREDENTIAL_FILE` | 空 | Windows 加密凭据文件 |
| `MARKET_DATA_CONFIG_FILE` | `.local/rqdata-market.json` | 采集器本地配置 |
| `DDB_HOST` / `DDB_PORT` / `DDB_USER` / `DDB_PASSWORD` | 空 | 构建 DDB seed 时需要 |

> `.env` 必须是 UTF-8（BOM 可有可无）。启动脚本按 UTF-8 读取；存成 ANSI/GBK 会读错中文注释。

## `config/*.json`

| 文件 | 内容 | 修改影响 |
|---|---|---|
| `basket.json` | 20 只核心 ETF | 改变日度净申赎历史，必须全量重建 |
| `universe.json` | 跟踪标的池、分类、`is_core_basket` 标记 | 影响宽基覆盖与图表范围 |
| `collection.json` | `flow_start`、`market_start`、`minute_days`、`concurrency` | 影响历史起点与分钟样本量 |
| `data-sources.json` | 日频源注册、默认源、能力矩阵 | 影响页面数据源下拉与就绪判断 |
| `etf-burst.json` | 放量分位参数、可选的 `symbols` 覆盖 | 影响逐 ETF 事件阈值 |

核心篮子的唯一来源是 `config/basket.json`。不要直接改 `etf_backend/constants.json` 或 `src/server/model.js`，使用 `scripts/basket.py` 校验并同步：

```powershell
python scripts\basket.py list
python scripts\basket.py add 510330.SH
python scripts\basket.py remove 159633.SZ
```

篮子变更后必须全量重建，不能用 `--recent`，并重启网页与采集器。

## 数据目录

历史快照、盘中缓存、拥挤度和放量归档默认写在 `data/private/`，该目录已被 `.gitignore` 忽略。四个变量支持绝对路径，可统一放到仓库外，例如 `D:\ftshare-data\`。

## 相关文档

- [实盘数据接入](live-setup.md)
- [数据管线](../operations/data-pipeline.md)
- [运维手册](../operations/runbook.md)

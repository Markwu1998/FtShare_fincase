# 文档目录

> 状态：现行 · 最后核对：2026-11-24

主运行方式：**RQData + Python 后端**（ETF 日频默认 DDB，RQData 可回退）。带「历史」标记的文档位于 `archive/`，仅供本地追溯，不随发布包分发。

## 项目入门

| 文档 | 内容 |
|---|---|
| [项目指南](overview/project-guide.md) | 功能、数据流、使用、复盘、部署及全 Python 改造评估 |
| [项目定位](overview/product.md) | 两个观测台、功能地图、运行边界 |
| [系统架构](overview/architecture.md) | 进程、数据流、模块边界、关键决策 |
| [快速开始](getting-started/quickstart.md) | 演示模式 5 分钟跑通 |

## 入门与配置

| 文档 | 内容 |
|---|---|
| [快速开始](getting-started/quickstart.md) | 演示模式启动 |
| [实盘数据接入](getting-started/live-setup.md) | RQData 凭据、首次历史、网页与采集器 |
| [配置参考](getting-started/configuration.md) | `.env` 与 `config/*.json` |

## 运行与运维

| 文档 | 内容 |
|---|---|
| [部署](operations/deployment.md) | Windows/Linux/CVM/Docker，网络与安全入口 |
| [运维手册](operations/runbook.md) | 首次安装、日常运行、健康检查、故障处理 |
| [单日 ETF 异动复盘](operations/runbook.md#34-单日-etf-异动复盘) | RQData 历史 ETF 与期货 Tick、基准准备、离线续跑与回放 |
| [日度主动成交与申赎对照](operations/runbook.md#35-日度主动成交与申赎对照) | 私有 CSV 导入、日期与标的筛选、配对汇总 |
| [数据管线](operations/data-pipeline.md) | 增量更新、收盘补全、回放、定时任务 |
| [Aces 监控日初准备](operations/monitor-preopen.md) | 独立 JSON、DDB 基准生成、覆盖校验与生产运行方式 |
| [备份、恢复与升级](operations/backup-and-upgrade.md) | 备份、迁移、升级回滚 |
| [网络与主机配置](operations/network-security.md) | HTTPS、反向代理、暴露面 |

## 数据与指标

| 文档 | 内容 |
|---|---|
| [数据契约](reference/data-contract.md) | 快照结构、字段、校验、增量合并 |
| [净申赎与资金口径](reference/flow-methodology.md) | 净申赎、规模、折溢价、主动成交对照、覆盖与偏差控制 |
| [ETF 异动雷达](reference/radar.md) | 20/60 日双基准、5 分钟窗口、回放统计 |
| [Tick 驱动异动监控](reference/tick-driver.md) | 默认分钟驱动、秒级候选、期货同窗、日志与复盘 |
| [市场拥挤度](reference/crowding.md) | 四个模块、采集、定时任务、数据源决策 |
| [图表数据接口](reference/chart-api.md) | 每张图对应的 RQData 方法与字段 |
| [HTTP API](reference/http-api.md) | 页面与 API 契约、状态码 |

## 数据源

| 文档 | 内容 |
|---|---|
| [RQData](reference/data-sources/rqdata.md) | 接口映射、代码与单位、权限 |
| [DDB](reference/data-sources/ddb.md) | 表结构、字段映射、完整性与一致性 |
| [日频数据源切换](reference/data-sources/switching.md) | 源注册、就绪判断、409 语义 |

## 开发

| 文档 | 内容 |
|---|---|
| [仓库目录](development/repo-layout.md) | 目录与模块职责 |
| [Aces IndexArb 监控窗口设计](development/indexarb-etf-burst.md) | ETF 异动与市场集中度窗口、策略列、共享行情及验收 |
| [测试](development/testing.md) | Python/Node 测试命令与要求 |
| [发布与验证](development/release.md) | 检查清单、公开发布边界、v0.1.0 记录 |

## 研究

| 文档 | 内容 |
|---|---|
| [ETF 盘中放量研究](research/etf-intraday-burst.md) | v1→v3 规则演进、样本与验收 |
| [成交额核算与 Tick 持续性评估](research/etf-intraday-factor-review-20261121.md) | 异常成交额重建、日末申赎配对与秒级候选 |

## 变更记录

- [CHANGELOG](../CHANGELOG.md)
- [source-version.json](source-version.json)

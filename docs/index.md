# 文档目录

主运行方式：**RQData + Python 后端**。带「遗留」标记的文档仅用于 Node/Cloudflare 回退或历史对照。

## 当前：运行与运维

| 文档 | 内容 |
|---|---|
| [运行与运维手册](operations.md) | 安装、日常运行、定时任务、数据目录、健康检查、故障处理、备份与升级 |
| [Python 后端](python-backend.md) | 采集器/网页启动、增量更新、收盘补全、回放、部署 |
| [部署方式](deployment.md) | Windows/Linux/腾讯云入口，Node 与 macOS 回退指引 |

## 当前：数据与图表

| 文档 | 内容 |
|---|---|
| [数据与计算](data.md) | RQData 接入、净申赎口径、快照结构、增量更新与容量 |
| [RQData / DDB 日频对照](data-sources-rqdata-ddb.md) | 两个日频源的接口映射、完整性、一致性与切换建议 |
| [日频数据源切换设计](data-source-switching-design.md) | 双源架构、API、一键切换语义与 UI 效果 |
| [图表接口](chart-interfaces.md) | 每张图对应的 RQData 方法与字段 |
| [雷达规则](radar-preview.md) | 20/60 日双基准、5 分钟窗口、状态判定 |
| [雷达窗口回放](radar-window-review.md) | 离线回放与诊断 |
| [盘中放量研究](etf-intraday-burst-research.md) | 逐 ETF 集中交易事件的规则与样本 |

## 当前：市场拥挤度

| 文档 | 内容 |
|---|---|
| [Python 后端 · 拥挤度](python-backend.md#市场拥挤度观测台) | `/crowding` 页面、四个模块、定时采集建议 |
| [图表接口 · 资金全景](chart-interfaces.md#日度资金全景) | 规模/折溢价视图口径 |

## 迁移与验收

| 文档 | 内容 |
|---|---|
| [上游模块迁移对照](market-data-provider-audit.md) | FtShare → RQData 逐接口对照与权限记录 |
| [自动刷新验收](auto-refresh-acceptance-20261112.md) | 历史验收记录 |
| [发布验证](verification.md) | 上线检查项 |

## 遗留：Node / Cloudflare / macOS

| 文档 | 内容 |
|---|---|
| [Node 启动](rqdata-market.md) | 旧 Node 采集与只读网页，回退用 |
| [Cloudflare Pages](cloudflare-pages.md) | Pages 只读部署（遗留） |
| [云端定时更新](cloud-scheduled-update.md) | 采集 Worker + KV + 私有归档（遗留） |
| [macOS](macmini.md) | LaunchAgents 管理（遗留） |

## 发布与安全

| 文档 | 内容 |
|---|---|
| [开源发布](open-source-release.md) | 公开仓库与私有运行仓库的边界 |
| [安全与网络](security-and-network.md) | 凭据、限流、反向代理与暴露面 |

## 变更记录

- [CHANGELOG](../CHANGELOG.md)
- [source-version.json](source-version.json)

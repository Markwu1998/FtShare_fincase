# 部署

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

主部署方式为 **Python 后端**：FastAPI 提供网页与只读 API，采集器独立运行。ETF 资金观测台与市场拥挤度观测台共用同一个 FastAPI 进程。

## 部署形态

| 目标 | 说明 |
|---|---|
| 本机 Windows | 使用 `scripts/python-backend.ps1` 启动网页与采集器 |
| Windows Server | 采集任务可用任务计划程序或 DSH 定时任务 |
| Linux 服务器 | Uvicorn + `python -m etf_backend.collect`，可用 systemd timer |
| 腾讯云 CVM | Windows 或 Linux 均可，采集调度方式随操作系统选择 |
| Docker | 仓库提供 `Dockerfile` 与 `compose.yaml`；多实例需要共享存储 |

## 基本步骤

1. 安装 Python 3.9 与 `requirements-python.txt`。
2. 配置 RQData 凭据与 `.env`，见 [配置参考](../getting-started/configuration.md)。
3. 首次构建历史快照：`history`；之后每日 `history --recent`。
4. 启动网页：监听 `127.0.0.1:8080`。
5. 启动盘中采集器：`live --watch`。
6. 按 [运维手册](runbook.md) 配置定时任务与健康检查。

## 网络与安全

- **不要把 8080 直接暴露到公网**：FastAPI 没有认证与限流，仅反向代理层可加。
- 对外使用 nginx/Caddy + HTTPS + 访问控制；SSE（`/api/etf-burst/stream`）需关闭代理缓冲。
- 采集器与网页分开权限和环境：RQData 凭据只在采集侧。
- 详见 [网络与主机配置](network-security.md)。

## 共享存储与多进程

- 单机部署可把采集器和 Uvicorn 作为不同进程运行，通过同一数据目录共享快照。
- 容器多实例部署需要共享存储，并把采集任务部署到独立的常驻或定时运行环境。
- 网页按文件版本热加载，数据更新不需要重启 FastAPI。

## 跨机器迁移

按数据授权迁移日频与拥挤度快照、`ETF_BURST_DIR` 归档，以及 `data/private/etf-flow-comparison.json` 专题快照。Windows 本机加密凭据在目标机器重新配置。`live` 模式应分别核验 `/api/board` 与 `/api/etf-flow-comparison` 的可用性；专题快照的口径和导入见[日度对照操作](runbook.md#35-日度主动成交与申赎对照)。

## 小程序 / 移动端

同一套 Python 后端与只读 API 可通过 HTTPS 域名供小程序访问。正式上线时由反向代理对外提供授权范围内的 API，Uvicorn 继续监听本机地址；按平台要求配置合法域名与备案。

## 遗留部署

Node、Cloudflare Pages/Worker、macOS LaunchAgents 仍可用于回退，文档已归档：

- [遗留 Node 路径总览](../archive/legacy-node/index.md)
- [Node 启动](../archive/legacy-node/node-setup.md)
- [Cloudflare Pages](../archive/legacy-node/cloudflare-pages.md)
- [云端定时更新](../archive/legacy-node/cloud-scheduled-update.md)
- [macOS](../archive/legacy-node/macos.md)

## 相关文档

- [数据管线](data-pipeline.md)
- [运维手册](runbook.md)
- [备份、恢复与升级](backup-and-upgrade.md)

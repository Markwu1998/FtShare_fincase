# 遗留 Node 路径

> 状态：历史 · 最后核对：2026-11-19

Node、Cloudflare Pages/Worker 与 macOS LaunchAgents 已不是主运行方式。保留这些文档用于**回退、对照和历史追溯**，新部署请使用 [Python 后端部署](../../operations/deployment.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [Node 启动](node-setup.md) | RQData Node 采集与只读网页，回退用 |
| [Cloudflare Pages](cloudflare-pages.md) | Pages 只读部署 |
| [云端定时更新](cloud-scheduled-update.md) | 采集 Worker + KV + 私有归档 |
| [macOS](macos.md) | LaunchAgents 管理 |

## 与当前路径的关系

- 主路径的 RQData 接口映射已整理到 [RQData 数据源](../../reference/data-sources/rqdata.md)。
- 主路径的部署与运维见 [部署](../../operations/deployment.md) 与 [运维手册](../../operations/runbook.md)。
- 遗留路径若与主路径冲突，以主路径文档为准。

## 返回

- [文档目录](../../index.md)
- [项目定位](../../overview/product.md)

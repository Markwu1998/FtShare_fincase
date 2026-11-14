# 部署方式

- 本地和新服务器部署使用 Python 后端。ETF 资金观测台与市场拥挤度观测台共用 FastAPI，后台采集独立运行。Windows、Linux、腾讯云 CVM 的部署及小程序接入见 [Python 后端启动和部署](python-backend.md)。

- 既有 Node 部署可继续使用原采集与只读网页入口，便于对照和回退。见 [Node 启动](rqdata-market.md)。
- Cloudflare Pages/Worker 继续使用现有 Node 构建与发布流程，见 [Cloudflare Pages](cloudflare-pages.md) 和 [后台发布](cloud-scheduled-update.md)。市场拥挤度页面与 API 当前由 FastAPI 提供。
- macOS 可用 LaunchAgents 管理只读网页和日度刷新。见 [macOS](macmini.md)。

Windows 使用 `scripts/python-backend.ps1` 启动网页或采集。Linux 可直接运行 Uvicorn 和 Python 采集模块。

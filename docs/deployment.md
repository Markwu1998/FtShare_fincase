# 部署方式

- 本地或服务器 Node 部署使用 Python RQData 采集器和 Node 只读网页。见 [本地启动](rqdata-market.md)。
- Cloudflare Pages/Worker 可部署生成的只读网页。Python 采集器运行在独立主机，发布历史快照及盘中 KV。见 [Cloudflare Pages](cloudflare-pages.md) 和 [后台发布](cloud-scheduled-update.md)。
- macOS 可用 LaunchAgents 管理只读网页和日度刷新。见 [macOS](macmini.md)。

Windows 本地直接使用 `npm.cmd run build`、`npm.cmd start`；后台盘中采集需单独启动。

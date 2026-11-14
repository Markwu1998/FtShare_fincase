# Cloudflare Pages 只读部署

先在具备 RQData Python SDK 的主机生成授权历史快照，设置 `DATA_MODE=live` 并运行 `npm run build:pages`。按命令输出发布 Pages 构建目录。网页提供历史数据和只读 API。实时缓存发布见 [后台发布](cloud-scheduled-update.md)。

不配置授权快照时可用演示模式验证部署。先确认数据授权和站点访问范围。

# macOS 后台运行

在可访问 RQData 的 Python 环境安装 `rqdatac` 与 `rqdatac-fund`，使用 SDK 凭据或进程环境配置。按 [采集文档](rqdata-market.md) 先生成历史快照，设定 `DATA_MODE=live` 和快照、缓存文件路径。

`npm run mac:install -- --dry-run` 可以生成 LaunchAgent 配置供检查。正式安装使用 `npm run mac:install`。日度刷新由管理任务执行，盘中采集独立运行 `node --env-file-if-exists=.env scripts/collect-live.mjs --watch` 并写入网页读取的同一缓存文件。

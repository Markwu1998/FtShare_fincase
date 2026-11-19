# RQData 后台采集与云端发布（遗留）

> 状态：历史 · 最后核对：2026-11-19

RQData Python SDK 在 Node/Python 采集主机执行。使用 [本地采集入口](node-setup.md) 生成历史快照，在授权部署环境重新构建并发布。日度任务的时间由部署者设置。

如需盘中 Cloudflare 缓存，将采集机本地缓存中的 `entries["live:rqdata:snapshot"]` 原样写到网页绑定的 KV 同名键。现有网页使用兼容的 `FTSHARE_CACHE` 绑定名。云端 KV 同步需要部署端配置，仓库的 Node 采集器默认只写本地文件。网页不持有 RQData 凭据。

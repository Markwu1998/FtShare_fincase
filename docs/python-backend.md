# Python 后端

采集器直接使用 RQData SDK，Python 计算历史资金和分钟篮子，FastAPI 提供现有 ECharts 页面与只读 API。采集器和网页独立运行，网页按文件版本加载已发布的快照。更新历史数据无需构建网页或重启服务。采集失败时保留原快照。

## 本地启动

在项目根目录建立 Python 虚拟环境，执行 `python -m pip install -r requirements-python.txt`。Windows 可将该环境的解释器路径设为 `ETF_BACKEND_PYTHON`。启动脚本也支持现有的 RQData 本机加密凭据配置。采集前按 [RQData 配置](rqdata-market.md) 设置本地配置文件，启用雷达时请提供至少 60 个完整的历史分钟日。

```powershell
$env:DATA_MODE = 'live'
# 采集历史，输出到 .env 配置的历史快照路径
& .\scripts\python-backend.ps1 history
# 网页默认在 8082，可由 PORT 或 .env 覆盖
$env:PORT = '8082'
& .\scripts\python-backend.ps1 web
```

在另一个终端启动 `& .\scripts\python-backend.ps1 -Mode live -Arguments @('--watch')`。盘中采集应保持单个进程，每分钟更新同一份缓存。验收阶段可为 Python 和原 Node 服务分别指定输出路径。

Linux 或已配置 SDK 凭据的 Python 环境可直接运行以下命令。环境变量只在相应组件的服务配置中设置，网页应使用只读的快照路径。

```sh
python -m etf_backend.collect history --config .local/rqdata-market.json
python -m etf_backend.collect live --watch
DATA_MODE=live ENABLE_RADAR=1 uvicorn etf_backend.web:app --host 127.0.0.1 --port 8082
```

回放用 `live --replay YYYY-MM-DD --cache <独立文件>` 生成缓存。网页同时配置该文件为 `FTSHARE_LIVE_CACHE_FILE`，并设 `RQDATA_ALLOW_REPLAY=1`。盘中模式应使用自己的缓存文件。

Windows 可直接用 `& .\scripts\python-backend.ps1 web -ReplayCache '.local/python-rqdata-replay.json'` 打开已生成的回放。普通 `web` 模式下，分时图、实时排行和成交雷达要等盘中采集器写入首份缓存。日度资金和历史图表读取历史快照。

生成指定日期的回放示例。

```powershell
& .\scripts\python-backend.ps1 -Mode live -Arguments @('--replay=2026-11-13','--cache=.local/python-rqdata-replay.json')
```

## API 与部署

现有网页使用 `/api/health`、`/api/board`、`/api/history`、`/api/live` 和 `/api/cumulative`。浏览器继续执行分钟回放和交互绘图。API 和采集器都能在无 Node.js 环境运行。

部署目标可以是另一台 Windows 电脑、Windows/Linux 服务器，或腾讯云 CVM。腾讯云 CVM 可选 Windows Server 或 Linux，采集调度方式随操作系统选择。单机部署可把采集器和 Uvicorn 作为不同进程运行，并通过同一数据目录共享快照。容器多实例部署需要共享存储，并应把采集任务部署到独立的常驻或定时运行环境。 [腾讯云 CVM 镜像列表](https://cloud.tencent.com/document/product/213/93093)

小程序可以复用同一套 Python 后端和只读 API，通过 HTTPS 域名访问，后台服务器可运行 Windows 或 Linux。正式上线时，让 HTTPS 反向代理对外提供授权范围内的 API，Uvicorn 继续监听本机地址。在微信公众平台配置 `request` 合法域名，腾讯云中国大陆部署同步确认备案要求。移动端按首屏、分时与历史范围拆分响应，发布前确认 RQData 与未来 Wind 数据的对外展示授权。 [小程序网络要求](https://intl.cloud.tencent.com/zh/document/product/1219/61745) · [腾讯云备案说明](https://cloud.tencent.com/document/api/243/37402)

## 市场拥挤度观测台

Python 网页服务在 `/crowding` 提供独立观测台，读取 `/api/crowding?days=250&table_days=5`。查询范围按交易日计，实际日期以已归档数据为准。历史分位使用截至各日近五年内的有效样本，市值比值均值和标准差使用当前图表区间。

首次采集默认回溯约一年。后续执行会补齐新日期并重查最近五个已归档交易日，单独写入 `data/private/rqdata-crowding.json`。可用 `CROWDING_FILE` 指定采集和服务共用的路径。需要指定回补起止时，Windows 用显式 `-Arguments` 数组传递参数。

```powershell
& .\scripts\python-backend.ps1 crowding
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=industry')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=funds')
& .\scripts\python-backend.ps1 -Mode crowding -Arguments @('--crowding-module=financing')
```

Linux 使用下表对应命令。可加 `--start=YYYY-MM-DD` 指定回补起点。附加模块首次回溯约一年，公募持仓按季度发布。采集器只发布北京时间当天之前的已结束交易日。尚无快照时对应 API 返回 503，补数完成后刷新页面即可查看。

### 后续指标与计划任务

`/api/crowding/industry`、`/api/crowding/funds`、`/api/crowding/financing` 各自读取独立快照。采集异常时保留上次归档。可用 `--crowding-module=industry|funds|financing` 单独补数。

- 指数行业用周末 RQData 申万一级分类。周市值用周末成分，周成交额逐日按有效成分汇总。固定申万 2014 版待 Wind API 核验，当前展示每期第一行业及峰值。
- 公募按 RQData 聚源 1101、1201 分类，合并有效多份额关系，取每只基金首次披露的前十大股中的 A 股。无法归类的持仓市值计入「未分类」。报告期用于横轴，公告日及基金覆盖独立披露。
- 融资使用沪深两市融资余额，市值为沪深 A 股本身的市值。名义 GDP 与 M2 按公布日期对齐。住户存款预留 Wind API 数据位，接入后应使用人民币余额及发布日期。

以下计划由后续部署机器的调度器配置。本机开发与预览时按需手动采集。

| 北京时间 | 模块 | 在目标机器上执行 |
| --- | --- | --- |
| 每天 07:40 | 成交额集中度与大小盘风格 | `python -m etf_backend.collect crowding --crowding-module=daily` |
| 每天 08:30 | 融资与市场估值 | `python -m etf_backend.collect crowding --crowding-module=financing` |
| 每周六 09:15 | 指数行业拥挤度 | `python -m etf_backend.collect crowding --crowding-module=industry` |
| 每周六 10:15 | 公募基金重仓集中度 | `python -m etf_backend.collect crowding --crowding-module=funds` |

**Windows 或 Windows Server** 使用任务计划程序，在目标机为每个模块建立一个任务。操作的「程序或脚本」填写目标机虚拟环境的 `python.exe` 绝对路径，「添加参数」填写表中 `-m etf_backend.collect …` 部分，「起始于」填写项目根目录。按目标机时区配置触发器，使执行时间对应表中的北京时间。用实际执行任务的账户配置 SDK 凭据，需在退出登录后运行时，选择「不管用户是否登录都要运行」，再用该账户验证取数与写快照。 [Windows 任务执行账户](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal?view=windowsserver2025-ps)

**Linux** 可为四个模块各配置一个 systemd oneshot service 和对应 timer。service 的 `WorkingDirectory` 设为项目根目录，`ExecStart` 使用目标机 Python 虚拟环境的绝对路径及表中参数。timer 的 `OnCalendar` 显式指定 `Asia/Shanghai`，例如 `*-*-* 07:40:00 Asia/Shanghai` 或 `Sat *-*-* 09:15:00 Asia/Shanghai`，并可设置 `Persistent=true` 补跑停机期间错过的触发。

两种部署都在目标机配置凭据，并让采集器与 FastAPI 使用同一个 `CROWDING_FILE` 基础路径。每个模块保留一个写入任务，通过调度器退出状态和日志排查失败。

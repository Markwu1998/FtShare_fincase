# 验证指南

## 自动检查

在项目的 Python 环境中验证 FastAPI、采集与计算接口。在已安装 Node 的开发机运行前端逻辑和构建校验。

```powershell
python -B -m unittest discover -s tests -p 'test_*.py'
npm ci
npm test
$env:DATA_MODE = 'demo'
npm run build
npm run validate
npm run check:release
```

自动测试覆盖沪深份额对齐、净申赎估算、缺失与折算处理、20／60日基准、午休窗口、日度归属、缓存共享、失败恢复和发布回退。测试使用合成数据。

## 页面检查

```powershell
$env:DATA_MODE = 'demo'
$env:PORT = '8080'
& .\scripts\python-backend.ps1 web
```

打开 [ETF 演示页面](http://127.0.0.1:8080)，检查九张图、文字导航、深浅主题和手机布局。Windows 有 Chrome 时，从项目根目录执行 `python -B tests/browser_tick_smoke.py`，使用隔离的本地服务验证 Tick 到网页、历史时点过滤、期货字段和主动成交对照面板。测试默认使用合成快照，有本地授权归档时也检查对应历史样本。

## 真实部署检查

按[部署指南](deployment.md)配置快照、后台采集与缓存，核对`/api/health`、`/api/board`和`/api/live`的模式、日期、成员及缓存时间；日度信号与归属日核对，20／60日基准使用完整历史分钟。

- 用 `live` 模式加载[单日 Tick 复盘](operations.md#34-单日-etf-异动复盘)，核对基准截止日、ETF 分钟覆盖、四期限期货覆盖、期货证据可见时间和日末申赎归属日。
- 导入[主动成交对照快照](operations.md#35-日度主动成交与申赎对照)，核对 `/api/etf-flow-comparison` 的日期区间和标的覆盖。筛选范围应同时应用于明细、按标的汇总和按日期汇总。对一条完整记录核对差额，对一条缺份额记录检查盘中金额、空白差额及汇总配对数。

以多个客户端同时读取缓存，核对响应和采集时间；通过隔离环境的出站计数检查网页读取与后台采集的分工。[自动刷新与并发验证](auto-refresh-acceptance-20261112.md)

HTTPS、自有域名、容器与操作系统调度在对应部署环境验收。沿用 Cloudflare 流程时另按[遗留部署文档](cloudflare-pages.md)验证。

## 源码包检查

```sh
npm run release:archive
```

核对SHA-256，将包解压到新目录，执行上面的自动与页面检查。逐文件清单与验证结果见候选目录的`releases/`。

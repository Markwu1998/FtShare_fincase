# ETF 资金与市场拥挤度观测台

**发现成交异动，跟踪宽基资金，从盘中观察到盘后复盘。**

**数据服务：RQData（日频可选择 DDB）**

Python 网页服务提供两个平级入口。ETF 资金观测台（`/`）用于观察盘中交易和 ETF 资金变化，市场拥挤度观测台（`/crowding`）用于跟踪沪深 A 股成交集中度、大小盘风格、指数行业、公募重仓及融资宏观指标。

[Python 启动、采集与回放](docs/python-backend.md) · [运行与运维手册](docs/operations.md) · [文档目录](docs/index.md) · [RQData 官方文档](https://www.ricequant.com/doc/rqdata/python/index-rqdatac)

**ETF 异动雷达（20/60 日双基准） · 20 只核心 ETF · 12 类宽基 · 9 张分析图 · 市场拥挤度观测台**

![ETF资金观测台预览](docs/preview.jpg)

## 用它观察市场

- **发现成交异动**：对照20日、60日历史同进度成交，观察累计放量与短时放量；支持阈值调整和时点回放。
- **比较盘中节奏**：将今日分钟成交与放量日、常态日对照，结合上证指数和核心ETF表现复盘。
- **跟踪宽基资金**：查看 12 类宽基 ETF 的申赎表、净申赎合计、日末规模和收盘折溢价，按时间窗口或自选日期比较。
- **观察市场拥挤度**：查看前 1%／3%／5% 个股成交额占比、沪深300／中证1000市值与成交额比值，以及定期更新的行业、公募持仓和融资指标。
- **持续查看市场**：行情自动刷新，多人共享同一份行情缓存。支持手机浏览、深浅主题和文字导航。

每张图均可查看数据来源与计算口径。[雷达规则](docs/radar-preview.md) · [资金计算](docs/data.md)

## 关键数据图

**宽基资金与区间累计** · 分类净申赎、区间累计与上证指数放在同一张图中。

![宽基分类资金、区间累计与上证指数](docs/screenshots/broad-flows.jpg)

**历史净申赎** · 按日期观察核心ETF的净申购、净赎回及大盘走势。

![核心ETF历史净申赎与上证指数](docs/screenshots/daily-netflow.jpg)

**分钟成交与指数** · 查看开盘、午后和尾盘的成交节奏。

![核心ETF分钟成交与上证指数](docs/screenshots/minute-turnover.jpg)

**累计成交对比** · 对照今日、所选历史日与近20日均值，观察放量程度。

![今日与历史日期的累计成交对比](docs/screenshots/cumulative-turnover.jpg)

**放量日与常态日** · 并排比较历史分钟结构，叠加今日成交曲线。

![放量日与常态日的分钟成交结构](docs/screenshots/minute-comparison.jpg)

**历史成交额** · 对照日度成交规模与上证指数，复盘交易活跃度。

![核心ETF历史成交额与上证指数](docs/screenshots/daily-turnover.jpg)

## 部署自己的看板

Python 采集与网页服务可独立运行。见 [Python 后端启动和部署](docs/python-backend.md)。

实盘模式需要具备相应权限的 RQData 账号，以及安装了 `rqdatac` 和 `rqdatac-fund` 的 Python 环境。后台采集器生成历史快照和盘中缓存，网页只读取发布结果。

[Windows／Linux／腾讯云部署](docs/deployment.md) · [运行与运维手册](docs/operations.md) · [旧 Node 启动与回退](docs/rqdata-market.md) · [上游模块迁移对照](docs/market-data-provider-audit.md)

## 本地体验

默认使用 Python 后端，本地演示无需行情账号。安装 Python 依赖后启动网页。

```powershell
python -m pip install -r requirements-python.txt
$env:DATA_MODE = 'demo'
$env:ENABLE_RADAR = '1'
$env:PORT = '8080'
& .\scripts\python-backend.ps1 web
```

打开 [http://127.0.0.1:8080](http://127.0.0.1:8080) 查看 ETF 演示样本。市场拥挤度页面需在真实数据模式下加载独立快照，配置见 [Python 后端](docs/python-backend.md)。

[图表接口](docs/chart-interfaces.md) · [数据与计算](docs/data.md) · [参与开发](CONTRIBUTING.md) · [更新日志](CHANGELOG.md)

[MIT 开源许可](LICENSE) · [第三方许可](THIRD_PARTY_NOTICES.md)

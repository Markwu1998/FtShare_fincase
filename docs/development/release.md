# 发布与验证

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

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
npm run deploy:check
```

自动测试覆盖沪深份额对齐、净申赎估算、缺失与折算处理、20／60 日基准、午休窗口、日度归属、缓存共享、失败恢复和发布回退。测试使用合成数据。

## 页面检查

```powershell
$env:DATA_MODE = 'demo'
$env:PORT = '8080'
& .\scripts\python-backend.ps1 web
```

打开 [ETF 演示页面](http://127.0.0.1:8080)，检查九张图、文字导航、深浅主题和手机布局。Windows 有 Chrome 时，从项目根目录执行 `python -B tests/browser_tick_smoke.py`，使用隔离的本地服务验证 Tick 到网页、历史时点过滤、期货字段和主动成交对照面板。测试默认使用合成快照，有本地授权归档时也检查对应历史样本。

## 真实部署检查

按 [部署](../operations/deployment.md) 配置快照、后台采集与缓存，核对：

- `/api/health` 的模式与数据源；
- `/api/board` 的日期、成员与覆盖；
- `/api/live` 的缓存时间与推进；
- 日度信号与归属日；
- 20／60 日基准使用完整历史分钟。

- 用 `live` 模式加载[单日 Tick 复盘](../operations/runbook.md#34-单日-etf-异动复盘)，核对基准截止日、ETF 分钟覆盖、四期限期货覆盖、期货证据可见时间和日末申赎归属日。
- 导入[主动成交对照快照](../operations/runbook.md#35-日度主动成交与申赎对照)，核对 `/api/etf-flow-comparison` 的日期区间和标的覆盖。筛选范围应同时应用于明细、按标的汇总和按日期汇总。对一条完整记录核对差额，对一条缺份额记录检查盘中金额、空白差额及汇总配对数。

以多个客户端同时读取缓存，核对响应和采集时间；通过隔离环境的出站计数检查网页读取与后台采集的分工。

HTTPS、自有域名、容器与操作系统调度在对应部署环境验收。沿用 Cloudflare 流程时另按[遗留部署文档](../archive/legacy-node/cloudflare-pages.md)验证。

## 源码包检查

```sh
npm run release:archive
```

核对 SHA-256，将包解压到新目录，执行上面的自动与页面检查。逐文件清单与验证结果见候选目录的 `releases/`。

## 公开发布边界

| 内容 | 位置 |
|---|---|
| 产品介绍、在线体验和使用入口 | [README](../../README.md) |
| 数据结构、净申赎计算与雷达要求 | [数据契约](../reference/data-contract.md)、[雷达](../reference/radar.md) |
| 部署与运维 | [部署](../operations/deployment.md)、[运维手册](../operations/runbook.md) |
| 模拟数据 CI、计算与缓存测试 | `.github/workflows/ci.yml`、`tests/` |
| 可选采集工作流 | `docs/examples/cloud-test-update.yml` |
| 许可与贡献 | [MIT](../../LICENSE)、[第三方声明](../../THIRD_PARTY_NOTICES.md)、[贡献](../../CONTRIBUTING.md) |

公开文件由 `scripts/lib/release.mjs` 生成允许清单。运行数据、凭据、原始响应和部署记录保存在私有运行环境。公开仓库运行模拟数据 CI，采集发布任务以模板形式交付。

## v0.1.0 发布记录

- 内容：ETF 异动雷达、20／60 日双基准、短时成交观察、阈值调整与时点回放；自动更新、后台统一采集与共享缓存；文字导航、九张图表、12 类宽基资金和手机浏览。
- 方式：后台采集、共享缓存、只读网页。部署者配置自己的 Key、历史数据、采集任务与缓存。
- 推送前：再次核对远端状态、版本和逐文件 SHA-256；将候选源码作为目标仓库的初始提交推送；核对 GitHub 展示、README 图片与链接、CI 结果。
- 网站部署、域名操作和数据归档按各自的发布流程执行。

## 相关文档

- [测试](testing.md)
- [备份、恢复与升级](../operations/backup-and-upgrade.md)
- [历史验收记录](../archive/verification/auto-refresh-20261112.md)

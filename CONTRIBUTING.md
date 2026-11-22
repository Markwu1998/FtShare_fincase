# 贡献说明

主后端是 Python（`etf_backend/`），运行在 conda `rqsdk` 环境；Node 仅用于遗留的构建/Cloudflare 路径。

## 环境与测试

```powershell
# Python 主后端（3.9）
& "D:\anaconda\envs\rqsdk\python.exe" -c "import rqdatac, rqdatac_fund, fastapi, uvicorn"
& "D:\anaconda\envs\rqsdk\python.exe" -m unittest discover -s tests -p "test_*.py"
& "D:\anaconda\envs\rqsdk\python.exe" -m compileall -q etf_backend
```

```powershell
# Node 遗留路径（可选）
npm ci
npm test
npm run build
npm run validate
npm run check:release
```

涉及部署配置时，再运行 `npm run deploy:check`；这是 dry-run，不发布。

- 图表改动同时检查深浅主题、窄屏、单位、双轴及缺失数据。
- 数据计算改动必须加入能区分正确与错误口径的合成样本，不依赖生产快照或真实 Key。
- `.env`、Key、缓存和运行数据保存在忽略目录或平台 Secret 中；提交通用配置示例。
- 更新成员分类需附上可查证来源，并说明对历史统计范围的影响。
- 修改计算版本时同步更新模型版本、数据校验与文档，为旧快照提供显式校验或迁移流程。
- 保留 ECharts 的第三方许可证与 NOTICE。

CI 无需 RQData 或云平台凭据。私有运行仓库另有需要 Secrets 的采集发布任务；公开仓库仅保留无 Key CI，定时部署以模板形式交付，见[开源指南](docs/open-source-release.md)。

## 雷达与缓存改动

- 雷达计算修改应覆盖同进度比较、20／60样本不足、零分母、缺失分钟、午休、开盘观察及日度归属日，不引入当前日／未来日作为基准。
- 修改阈值须更新规则说明；回放结果按触发频率与样本范围报告。避免用成交额推断净流入或交易主体。
- 网页回放和阈值控件只读取缓存。单日复盘命令可显式用 RQData 补齐历史基准与 Tick；测试使用合成数据或本机授权快照。
- 验证构建开关 `ENABLE_RADAR`、导航、主题和数据日期；正式发布继续使用固定源码和测试通过的同一归档。

## 单日复盘维护

新增或修改盘中监控指标、盘中证据或落库字段时，同一变更要更新实时和单日回放路径，并同步更新[计算与输入说明](docs/tick-driver.md#tick-数据回放)和[运维操作](docs/operations.md#34-单日-etf-异动复盘)。

1. 优先复用实时计算逻辑。记录新增输入字段、单位、窗口边界、可用时刻、历史样本范围及缺数表现；规则变更时更新兼容性检查和基准版本。历史基准只用目标日前的数据。
2. 核对 JSON 归档、SQLite 日志与网页回放中的新字段。复盘时钟要限制证据的显示时刻；旧归档和缺字段时显示未覆盖，避免用零值补出信号。
3. 用固定输入验证新增指标的数值、触发时刻、金额去重与落库读回。区分行情时间和接收时间，并记录历史日期实测的覆盖和差异。离线回归使用 `tests/test_single_day_replay.py`，真实 RQData 复盘独立执行。

修改主动成交对照的字段或筛选行为时，同步更新[输入与计算口径](docs/data.md#主动成交与净申赎对照)和[导入操作](docs/operations.md#35-日度主动成交与申赎对照)。验证日期与标的筛选、有效配对数、缺失值和零值。

数据服务名称统一使用 **RQData**；项目名称使用 **ETF 资金与市场拥挤度观测台**。运行与定时任务见[运维手册](docs/operations.md)。

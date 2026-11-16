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
- 网页、回放、阈值控件只能读取缓存，不增加供应商请求；测试中使用合成数据或本机授权快照，不能修改正式行情测试。
- 验证构建开关 `ENABLE_RADAR`、导航、主题和数据日期；正式发布继续使用固定源码和测试通过的同一归档。

数据服务名称统一使用 **RQData**；项目名称使用 **ETF 资金与市场拥挤度观测台**。运行与定时任务见[运维手册](docs/operations.md)。

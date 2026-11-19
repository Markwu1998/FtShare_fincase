# 测试

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

## Python 主后端

```powershell
& "D:\anaconda\envs\rqsdk\python.exe" -c "import rqdatac, rqdatac_fund, fastapi, uvicorn"
& "D:\anaconda\envs\rqsdk\python.exe" -m unittest discover -s tests -p "test_*.py"
& "D:\anaconda\envs\rqsdk\python.exe" -m compileall -q etf_backend
```

## Node 遗留路径（可选）

```powershell
npm ci
npm test
npm run build
npm run validate
npm run check:release
```

涉及部署配置时再运行 `npm run deploy:check`；这是 dry-run，不发布。

## 测试要求

- 数据计算改动必须加入能区分正确与错误口径的合成样本，不依赖生产快照或真实 Key。
- 图表改动同时检查深浅主题、窄屏、单位、双轴及缺失数据。
- 雷达计算修改应覆盖同进度比较、20／60 样本不足、零分母、缺失分钟、午休、开盘观察及日度归属日。
- 网页、回放、阈值控件只能读取缓存，不增加供应商请求。
- 测试使用合成数据或本机授权快照，不能修改正式行情。

## 相关文档

- [发布检查](release.md)
- [仓库目录](repo-layout.md)

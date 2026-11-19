# 快速开始（演示模式）

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

演示模式不需要行情账号，用程序生成的样本数据跑通页面，适合本地体验与前端调试。

## 前置条件

- Windows 10/11 或 Linux
- Python 3.9（推荐 conda 环境 `rqsdk`）
- 已安装依赖：

```powershell
python -m pip install -r requirements-python.txt
```

## 启动

```powershell
$env:DATA_MODE = 'demo'
$env:ENABLE_RADAR = '1'
$env:PORT = '8080'
& .\scripts\python-backend.ps1 web
```

浏览器打开 <http://127.0.0.1:8080>。

- `/`：ETF 资金观测台演示样本。
- `/crowding`：市场拥挤度页面需要真实数据模式下的独立快照，演示模式可能为空。

## 停止与重启

在控制台按 `Ctrl+C`。改动 `etf_backend/` 代码后需要重启网页进程；仅更新数据文件不需要重启。

## 下一步

- 接入实盘行情：[实盘数据接入](live-setup.md)
- 了解配置项：[配置参考](configuration.md)
- 日常运行与故障处理：[运维手册](../operations/runbook.md)

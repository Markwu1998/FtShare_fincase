# 备份、恢复与升级

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

## 备份

建议每次大版本升级前、以及每月一次。

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
Copy-Item data\private "D:\ftshare-backup\data-$stamp" -Recurse
Copy-Item .env "D:\ftshare-backup\env-$stamp.txt"
Copy-Item .local\rqdata-market.json "D:\ftshare-backup\rqdata-market-$stamp.json"
```

## 恢复

1. 停网页与采集器；
2. 把备份的 `data/private` 覆盖回去；
3. 恢复 `.env`；
4. 启动网页，`status.cmd` 核对日期；
5. 需要时跑一次 `history --recent` 补齐。

## 迁移到新机器

1. 安装 conda 环境与 `requirements-python.txt`；
2. 配置 `RQDATAC2_CONF`（RQData 凭据）；
3. 复制 `data/private`、`.env`、`.local/rqdata-market.json`；
4. 注册计划任务（`setup-tasks.cmd`）；
5. `status.cmd` 验证。

## 升级与回滚

- 快照带 `schema:1` 与 `formula` 版本；**跨公式版本不能直接混用**，需全量重建。
- 升级 `rqdatac` / `rqdatac_fund` 前先备份依赖：

```powershell
& "D:\anaconda\envs\rqsdk\python.exe" -m pip freeze > .local\rqsdk-before.lock.txt
```

- 升级后必须复测：

```powershell
& "D:\anaconda\envs\rqsdk\python.exe" -m unittest discover -s tests -p "test_*.py"
& .\scripts\python-backend.ps1 history -Arguments @('--recent')
```

- 回滚：还原 `data/private` + `pip install -r .local\rqsdk-before.lock.txt`。
- 网页升级不需要重启数据采集；但 `etf_backend` 代码改动需重启网页进程。

## 相关文档

- [运维手册](runbook.md)
- [数据契约](../reference/data-contract.md)

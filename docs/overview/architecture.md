# 系统架构

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

## 进程与职责

| 角色 | 入口 | 端口 | 读写 | 说明 |
|---|---|---|---|---|
| 网页服务 | `scripts/python-backend.ps1 web` | 8080 | 只读 | FastAPI；ETF 页 `/`，拥挤度页 `/crowding` |
| 盘中采集器 | `scripts/python-backend.ps1 -Mode live -Arguments @('--watch')` | 无 | 只写 | RQData 推送；09:15 预载基准，09:30 订阅 |
| 日频更新 | `scripts/python-backend.ps1 history -Arguments @('--recent')` | 无 | 只写 | 收盘后或定时更新历史快照 |
| 拥挤度采集 | `scripts/python-backend.ps1 crowding` | 无 | 只写 | 日度、行业、公募、融资四个独立归档 |
| 逐 ETF 放量 | 由采集器自动写入 | 无 | 只写 | 实时事件与每日归档 |

## 数据流

```text
RQData SDK ──► 采集器（历史/盘中/拥挤度）──► data/private/*.json
                                                   │
浏览器 ◄── FastAPI 只读 API ◄── 快照与缓存（按文件版本热加载）
```

- **一个请求只读一个 seed**：`/api/board?source=ddb` 不会混读 RQData 的日频字段。
- **实时与日频分离**：`/api/live` 读盘中缓存；日频切换不影响实时源。
- **网页不访问 RQData**：所有上游调用都在采集侧完成，页面交互只重算缓存数据。

## 代码边界

| 目录 | 职责 |
|---|---|
| `etf_backend/` | Python 主后端：采集、模型、数据源适配、实时订阅、拥挤度、Web API |
| `src/` | 前端页面与静态资源；`src/server/`、`src/background/` 为遗留 Node 路径 |
| `scripts/` | 启动、构建、采集、发布、校验等运维脚本 |
| `config/` | 篮子、标的池、采集区间、数据源注册、放量参数 |
| `tests/` | Node 与 Python 测试 |
| `docs/` | 本目录 |

更细的模块职责见 [仓库目录](../development/repo-layout.md)。

## 关键设计决策

1. **文件快照而非数据库**：采集结果写入 JSON，网页按 `(inode, size, mtime)` 热加载，升级数据不需要重启服务。
2. **只读网页**：FastAPI 不提供写接口，也不持有 RQData 凭据；对外暴露必须经过反向代理与访问控制。
3. **数据契约带版本**：快照包含 `schema`、`formula`、`flow_policy`，跨公式版本不能直接混用。
4. **口径可追溯**：净申赎、规模、折溢价由统一模型计算，历史校正记录随快照保留。

## 相关文档

- [HTTP API 契约](../reference/http-api.md)
- [数据契约](../reference/data-contract.md)
- [净申赎与资金口径](../reference/flow-methodology.md)
- [数据管线](../operations/data-pipeline.md)

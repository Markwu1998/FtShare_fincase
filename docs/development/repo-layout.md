# 仓库目录与模块职责

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

## 顶层目录

| 路径 | 职责 |
|---|---|
| `etf_backend/` | Python 主后端：采集、模型、数据源适配、实时订阅、拥挤度、Web |
| `src/` | 前端页面与静态资源；`src/server/`、`src/background/` 为遗留 Node 路径 |
| `scripts/` | 启动、构建、采集、发布、校验脚本 |
| `config/` | 篮子、标的池、采集区间、数据源注册、放量参数 |
| `tests/` | Node 与 Python 测试 |
| `docs/` | 文档 |
| `data/` | 演示快照；`data/private/` 为被忽略的运行数据 |
| `third_party/` | 第三方许可证与通知 |
| `.github/workflows/` | CI |

## `etf_backend/`

| 模块 | 职责 |
|---|---|
| `collect.py` | 采集命令入口：历史、盘中、收盘补全、回放、拥挤度、放量回放 |
| `provider.py` | RQData 数据获取 |
| `sources.py` | 日频源注册与适配（RQData / DDB） |
| `model.py` | 净申赎、宽基聚合、快照模型与校验 |
| `realtime.py` | 盘中分钟/tick 订阅、缓存写入与看门狗 |
| `burst.py` | 逐 ETF 放量事件计算 |
| `crowding.py` / `crowding_extra.py` | 拥挤度四个模块 |
| `web.py` | FastAPI 路由、快照热加载、只读 API |
| `constants.json` | 由篮子脚本同步的常量（不手工编辑） |

## `src/`

| 路径 | 职责 |
|---|---|
| `index.html` / `crowding.html` | 两个页面入口 |
| `assets/dashboard.js` | ETF 资金观测台交互与绘图 |
| `assets/crowding.js` | 拥挤度页面 |
| `assets/radar.js` / `radar-math.js` | 雷达展示与计算 |
| `assets/data-source.js` | 数据源切换 UI |
| `server/`、`background/` | 遗留 Node 服务与采集器，仅回退使用 |

## 配置边界

- 核心篮子只改 `config/basket.json`，用 `scripts/basket.py` 同步。
- 标的池与分类只改 `config/universe.json`。
- 数据源注册只改 `config/data-sources.json`。
- 运行参数与路径放在 `.env`，不入库。

## 相关文档

- [系统架构](../overview/architecture.md)
- [配置参考](../getting-started/configuration.md)
- [测试](../development/testing.md)

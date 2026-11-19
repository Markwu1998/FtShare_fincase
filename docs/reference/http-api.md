# HTTP API

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

FastAPI 只提供 GET/HEAD 只读接口。所有日频接口读取已发布的快照文件；实时接口读取采集器写入的盘中缓存。

## 页面

| 路径 | 页面 |
|---|---|
| `/` | ETF 资金观测台 |
| `/crowding` | 市场拥挤度观测台 |

## 日频与通用

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 模式、数据源、快照时间等健康信息 |
| GET | `/api/sources` | 日频/实时源列表、就绪状态与原因 |
| GET | `/api/board?source=rqdata\|ddb` | 看板主数据；源未就绪返回 409 `source_not_ready` |
| GET | `/api/history` | 核心 ETF 历史序列 |
| GET | `/api/cumulative` | 区间累计 |
| GET | `/api/daily` | 日度资金全景 |

`source` 只影响该请求读取的快照，不会混读两个源。快照缺失或校验失败返回 503。

## 实时

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/live` | 当日盘中分钟缓存；盘前、跨日或采集器未运行时返回 503 |
| GET | `/api/etf-burst` | 指定日期的逐 ETF 放量事件；`?date=YYYY-MM-DD` |
| GET | `/api/etf-burst/dates` | 可用日期列表；雷达未启用返回 404 |
| GET | `/api/etf-burst/stream` | SSE 事件流；反向代理需关闭响应缓冲 |

## 拥挤度

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/crowding?days=250&table_days=5` | 日度集中度/风格 |
| GET | `/api/crowding/industry` | 行业拥挤度 |
| GET | `/api/crowding/funds` | 公募重仓 |
| GET | `/api/crowding/financing` | 融资与估值 |

## 状态码约定

| 状态码 | 含义 |
|---|---|
| 200 | 正常 |
| 404 | 资源未启用（如雷达关闭） |
| 409 | 数据源未就绪（`source_not_ready`） |
| 503 | 快照/缓存缺失或校验失败 |

## 相关文档

- [数据契约](data-contract.md)
- [数据源切换](data-sources/switching.md)
- [市场拥挤度](crowding.md)

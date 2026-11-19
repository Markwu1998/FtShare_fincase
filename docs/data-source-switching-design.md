# 日频数据源可切换改造设计

> 状态：双源入口和基础适配器已实施；本文也包含尚未落地的目标设计。RQData 默认，DDB 受数据更新和适配完整性约束。
> 默认方案：**RQData**  
> 实时行情：当前为 **RQData**；实时源下拉已预留，后续可扩展其他实时源。  
> 关联文档：[RQData / DDB 日频对照](data-sources-rqdata-ddb.md)

> 2026-11-19 优化后：已增加 `fund_nav` 历史同日净值回退、`NET_INFLOW` 对账、SPLIT_RATIO 累乘因子、逐只目标日价格/成交额/份额/净值检查。DDB 使用已观察到的上证指数交易日期处理周末/节假日，尚不能识别整个指数源也漏掉的交易日；`fund_div` 独立交叉核对仍待真实数据验证。当前证据与限制见 [优化与验收](tick-live-20261119.md)，原始问题见 [合并审查](merge-review-20261119.md)。

## 1. 目标

1. 日频数据支持 **RQData / DDB** 两个来源，用户可在页面上**一键切换**；
2. 默认使用 **RQData**；
3. 页面显式显示当前数据源、数据日期、来源状态；
4. 切换不重启服务、不刷新整页、不混合两个来源的同一条指标；
5. DDB 未就绪（如 `fund_etf_nr` 未更新）时，明确禁用/提示，不静默回退；
6. 保留现有快照、图表、净申赎口径和 DDB/RQData 一致性校验结果。

## 2. 非目标

- 本期不接入其他实时源，但 UI 预留“实时行情源”下拉；
- 不做多用户/多租户数据源隔离；
- 不做两源逐字段实时拼接（同一份日频快照只来自一个源）；
- 不改变现有净申赎公式；
- Node/Cloudflare 遗留路径暂不扩展，Python 后端优先。

## 3. 总体架构

```text
浏览器
  │  GET /api/sources
  │  GET /api/board?source=ddb
  ▼
FastAPI（web.py）
  │  SourceRegistry
  │    ├─ daily_sources
  │    │    ├─ rqdata → data/private/rqdata-seed.json
  │    │    └─ ddb    → data/private/ddb-seed.json
  │    └─ realtime_sources
  │         └─ rqdata → data/private/rqdata-live.json
  ▼
日频快照（同一 schema，source 字段不同）
  ▲
  │  history --source rqdata|ddb
  │
采集层 SourceAdapter
  ├─ RQDataAdapter（包装现有 provider.py）
  └─ DDBAdapter（DolphinDB / hermes_etf_data）
```

关键原则：

- **一个请求只读一个 seed**；`/api/board?source=ddb` 不会读取 RQData seed 的日频字段；
- **实时与日频分开标识**：两者各自选择；本期实时源只有 RQData，切换日频不影响 `/api/live`；
- 每个源独立构建、独立校验、独立记录 `built_at` / `latest_flow_date` / 能力；
- 切换是**读已构建快照**，不在请求里触发全量采集。

## 4. 数据契约

### 4.1 Seed 元数据

在现有 seed 顶层增加：

```json
{
  "schema": 1,
  "provider": "DDB",
  "source": {
    "id": "ddb",
    "label": "DDB",
    "kind": "daily",
    "capabilities": ["bars", "turnover", "shares", "nav", "splits", "calendar", "premium"],
    "realtime_source": {"id": "rqdata", "label": "RQData"},
    "built_at": "2026-11-18T01:26:31Z",
    "latest_market_date": "2026-11-17",
    "latest_flow_date": "2026-11-17",
    "readiness": "ready"
  },
  "source_audit": {
    "daily_source": "DDB",
    "realtime_source": "RQData",
    "checks": [
      {"name": "fund_etf_nr_freshness", "ok": true},
      {"name": "fund_div_freshness", "ok": true}
    ]
  }
}
```

- `provider` 保留给现有页面/校验；
- `source` 是新的机器可读元数据；
- `readiness`：`ready` / `partial` / `stale` / `unavailable`。

### 4.2 统一日频输入

`history_snapshot` 不直接依赖 RQData 的 DataFrame，而接收统一结构：

```python
{
  "calendar": ["2026-11-17", ...],
  "bars": [{"symbol": "510050.SH", "date": "2026-11-17", "close": 2.884,
            "turnover": 2057385000.0, "volume": 716273600.0}, ...],
  "index": [{"date": "2026-11-17", "close": 3584.5794}, ...],
  "funds": {
    "510050.SH": {
      "shares": [{"date": "2026-11-17", "value": 6852367000.0}],
      "navs": [{"date": "2026-11-17", "unit_nav": 2.8868, "factor": 1.0}],
      "splits": [{"date": "2024-09-20", "ratio": 0.499776}]
    }
  }
}
```

这样 `model.history_snapshot`、`fund_flows`、宽基聚合、核心表都能复用，不需要为 DDB 复制一套计算逻辑。

### 4.3 能力矩阵

| 能力 | RQData | DDB |
|---|---|---|
| `bars` 收盘价 | ✅ | ✅ |
| `turnover` 成交额 | ✅ | ✅ |
| `volume` 成交量 | ✅ | ✅ |
| `index` 指数 | ✅ | ✅ |
| `nav` 单位净值 | ✅ | ✅ |
| `premium` 折溢价 | ✅ | ✅（close/nav） |
| `shares` 总份额 | ✅ | ⚠️ 依赖 `fund_etf_nr` 恢复更新 |
| `netflow` 净申赎 | ✅ | ⚠️ 依赖 `fund_etf_nr` |
| `scale` 规模 | ✅ | ⚠️ 依赖 `fund_etf_nr` |
| `splits` 拆分事件 | ✅ | ✅（`fund_div`，需保持更新） |
| `calendar` 交易日历 | ✅ | ⚠️ 需确认 `md_trade_cal` 或改用行情日期 |
| 实时行情 | ✅ | ❌ 本期不接 |

**DDB readiness 规则：**

```text
ready = 20 只核心 ETF 的 fund_etf_nr.max(TRADE_DATE) >= 目标 flow 日期
        且 fund_div/行情/净值可用
```

任一条不满足：

- `/api/sources` 标 `ready=false` + `reason`；
- UI 的 DDB 按钮置灰；
- 如果 URL 强制 `?source=ddb`，返回 `409 source_not_ready`，不返回旧 DDB 数据。

## 5. 代码结构与改造点

### 5.1 新增模块

```text
etf_backend/
  sources/
    __init__.py
    base.py        # DailySource 协议、能力、状态对象
    rqdata.py      # 包装现有 provider.py
    ddb.py         # DolphinDB 查询与字段映射
    registry.py    # 配置加载、source 选择、ready 检查
config/
  data-sources.json
```

`base.py`：

```python
class DailySource(Protocol):
    id: str
    label: str
    capabilities: set[str]
    def status(self, target_date: str) -> SourceStatus: ...
    def collect(self, start: str, end: str, minute_days: int) -> DailyBundle: ...
    def fund_history(self, symbols: list[str], start: str, end: str) -> dict: ...
```

### 5.2 RQDataAdapter

直接复用现有：

- `provider.collect(...)`
- `provider.fund_history(...)`
- `provider.traded_closes(...)`
- `model.fund_flows(...)`

输出统一 `DailyBundle`，行为保持不变，先把现网逻辑“包一层”，降低回归风险。

### 5.3 DDBAdapter

| 统一字段 | DDB 表/字段 |
|---|---|
| ETF 收盘/成交额/成交量 | `mkt_fundd.CLOSE_PRICE`、`TURNOVER_VALUE`、`TURNOVER_VOL` |
| 指数收盘 | `AIndexEODPrices.S_DQ_CLOSE` |
| 份额 | `fund_etf_nr.SHARES` |
| 单位净值 | `fund_etf_nr.NAV`；缺失时 `fund_nav.NAV` |
| 拆分 | `fund_etf_nr.SPLIT_RATIO`；`fund_div` 交叉核对 |
| 净申赎 | 用现有 `fund_flows` 公式从 shares/nav/split 计算；`NET_INFLOW` 做断言校验 |
| 代码映射 | `fund_etf_info.TICKER_SYMBOL` + `EXCHANGE_CD` |

注意：

- 不能因为 `NET_INFLOW` 是现成字段就直接替代公式，否则拆分/缺失日口径会和 RQData 分叉；
- 用 `NET_INFLOW` 作为**一致性断言**：差异超过阈值就让 DDB 构建失败；
- `fund_etf_nr` 必须覆盖全部 20 只核心，且最后日期 >= 目标 flow 日期；
- DDB 查询按日期分区、批量代码查询，避免全表扫描。

### 5.4 采集命令

```powershell
# 现有 RQData，保持兼容
& .\scripts\python-backend.ps1 history --source rqdata --recent

# DDB
& .\scripts\python-backend.ps1 history --source ddb --recent
```

新增参数：

- `--source rqdata|ddb`，默认 `rqdata`；
- `--source-config config/data-sources.json`；
- 构建失败输出 `source_not_ready` 与缺失表/日期，不覆盖旧快照。

### 5.5 服务端

`web.py`：

1. 启动时加载 `SourceRegistry`；
2. `Archive` 支持多 seed 路径；
3. `/api/board?source=...` 选择 seed；
4. `/api/sources` 返回日频源 + 实时源的就绪状态；
5. `/api/live?realtime_source=rqdata` 选择实时缓存；本期仅支持 `rqdata`；
6. `validate_seed` 校验 `seed.provider` / `source.id` 与 source 一致；
7. 响应头增加 `X-Data-Source` 与 `X-Realtime-Source`。

`/api/health` 增加：

```json
{
  "daily_sources": ["rqdata", "ddb"],
  "default_source": "rqdata",
  "realtime_default": "rqdata",
  "realtime_sources": {
    "rqdata": {"ready": true, "latest_minute": "14:05"}
  },
  "sources": {
    "rqdata": {"ready": true, "latest_flow_date": "2026-11-17"},
    "ddb": {"ready": false, "reason": "fund_etf_nr last 2026-07-14"}
  }
}
```

### 5.6 配置

`config/data-sources.json`：

```json
{
  "default": "rqdata",
  "realtime_default": "rqdata",
  "sources": [
    {
      "id": "rqdata",
      "label": "RQData",
      "kind": "daily",
      "seed": "data/private/rqdata-seed.json",
      "capabilities": ["bars", "turnover", "volume", "index", "nav", "premium", "shares", "netflow", "scale", "splits", "calendar"]
    },
    {
      "id": "ddb",
      "label": "DDB",
      "kind": "daily",
      "seed": "data/private/ddb-seed.json",
      "capabilities": ["bars", "turnover", "volume", "index", "nav", "premium", "shares", "netflow", "scale", "splits"]
    }
  ],
  "realtime_sources": [
    {
      "id": "rqdata",
      "label": "RQData",
      "kind": "realtime",
      "cache": "data/private/rqdata-live.json",
      "capabilities": ["bars", "tick", "index"]
    }
  ]
}
```

环境变量：

- `FTSHARE_SOURCE_CONFIG` 覆盖配置路径；
- `FTSHARE_SEED_FILE` 仍作为 RQData 的兼容路径；
- DDB seed 路径由配置指定，避免破坏现有部署。

## 6. 切换语义

### 6.1 选择优先级

```text
URL ?source=ddb
  > localStorage ftshare-daily-source
  > config.default（rqdata）
```

### 6.2 状态机

```text
idle → checking → switching → ready
                 ↘ error（保留原 source）
```

- 点击切换：
  1. 若目标 `ready=false`，不切换，展示原因；
  2. 若 ready，显示轻量 loading（图表上覆盖“正在切换到 DDB…”）；
  3. `GET /api/board?source=ddb`；
  4. 成功：更新 `seed`、URL、localStorage、来源徽标、图表来源说明；
  5. 失败：保留原 source，toast + 原因，绝不半套混用。
- 切换不刷新页面、不重启服务；
- 两个 seed 都预构建时，切换是本地请求，几百毫秒级。

### 6.3 实时与日频的关系

- 页面顶部有两个独立下拉：
  - `日频数据源`：RQData / DDB；
  - `实时行情源`：当前只有 RQData，位置和交互预留；
- 两个来源独立选择，互不影响；
- 切换日频源不影响 `/api/live`；
- 分时/成交雷达显示当前实时源，例如“实时 RQData”；
- 日度净申赎/宽基累计使用当前日频源；
- 本期实时源没有第二选项，下拉中只展示 RQData，其他项显示“未接入”。

实时源切换预留：

```http
GET /api/live?realtime_source=rqdata
```

- 服务端按 `realtime_source` 读取对应缓存；
- 当前只有 RQData 缓存；
- 未来新增实时源时，增加缓存路径与适配器即可，前端不用改布局。

## 7. UI 设计

### 7.1 页头布局

```text
┌──────────────────────────────────────────────────────────────────────┐
│ ETF FLOW              日频数据源 [RQData ▾]   实时行情源 [RQData ▾] │
│ ETF 资金观测台        行情 14:05 · 延时 3秒 · 本地 14:05              │
│                                                                      │
│  [日频：RQData · 2026-11-17]        [实时：RQData]                   │
├──────────────────────────────────────────────────────────────────────┤
│ 核心 ETF 成交额     宽基最新净流入     2026 宽基累计     追踪覆盖    │
└──────────────────────────────────────────────────────────────────────┘
```

设计原则：

- 页面品牌只保留 **ETF FLOW / ETF 资金观测台**，不再出现 `RQData` 品牌；
- 日频源和实时源各有一个**下拉菜单**，两者独立；
- 实时源下拉目前只有 RQData，但位置和交互先预留；
- 实时源标识用更小的灰色徽标；
- 页头留白增加，减少边框和颜色块，整体更简洁。

### 7.2 日频数据源下拉菜单

```text
日频数据源
┌──────────────────────────────┐
│ RQData          ● 已就绪  ✓ │
│ 2026-11-17 · 默认            │
├──────────────────────────────┤
│ DDB             ● 未就绪    │
│ 份额 2026-07-14 · 暂不可选   │
├──────────────────────────────┤
│ 数据源说明…                  │
└──────────────────────────────┘
```

交互：

- 点击页头 `RQData ▾` 展开；
- 当前源高亮，左侧状态点：
  - 绿色 ready
  - 琥珀 stale/partial
  - 灰色 unavailable
- 不可用源仍展示，但置灰、不可点击，并显示原因；
- 选择可用源立即切换，无需确认弹窗；
- 菜单底部固定“数据源说明…”入口；
- 键盘：`↑/↓` 移动、`Enter` 选择、`Esc` 关闭；ARIA `listbox`；
- 移动端下拉菜单贴边铺开，最大高度 60vh。

相比分段控件的优势：

- 两源时不显拥挤；未来加第三个源不需要重排页头；
- 可以承载“状态、日期、默认、不可用原因”等次级信息；
- 视觉更轻，符合“简练”的要求。

### 7.3 实时行情源下拉菜单

```text
实时行情源
┌────────────────────────────────────┐
│ RQData   实时推送 · 已就绪      ✓ │
│ 分时 / 成交雷达 / 实时表格         │
├────────────────────────────────────┤
│ 更多实时源（预留）                  │
│ 后续接入后自动出现在这里            │
└────────────────────────────────────┘
```

设计：

- 当前只有 `RQData` 一个可选项；
- 下拉位置、状态点、选中态、键盘交互与日频源一致；
- 点击其他未接入项时只提示“该实时源尚未接入”，不改变当前实时源；
- 实时源选择只影响 `/api/live`，不影响日频 `/api/board`；
- 页头来源标识用小号灰字：`实时：RQData`；
- 未来新增实时源时，只需：
  1. `config/data-sources.json` 增加 `realtime_sources` 条目；
  2. 增加对应实时缓存/采集适配器；
  3. UI 下拉自动渲染，无需改布局。

实时源 API 预留：

```http
GET /api/live?realtime_source=rqdata
GET /api/sources
```

`/api/sources` 返回：

```json
{
  "realtime_default": "rqdata",
  "realtime_sources": [
    {
      "id": "rqdata",
      "label": "RQData",
      "kind": "realtime",
      "ready": true,
      "capabilities": ["bars", "tick", "index"]
    }
  ]
}
```

### 7.4 品牌与文案简化

| 位置 | 现状 | 调整 |
|---|---|---|
| `<title>` | `RQData · ETF资金观测台` | `ETF 资金观测台` |
| 页头品牌 | `RQData` + `ETF FLOW` | **`ETF FLOW`**，下方 `ETF 资金观测台` |
| 右上角链接 | `RQData 文档` | `数据说明`（打开来源详情） |
| 图表来源摘要 | `RQData 数据接口` | `数据源与字段` |
| 页脚 | `数据服务：RQData。…` | `日频数据随所选来源切换；盘中行情由实时源提供。` |
| 来源策略说明 | 固定 RQData 文案 | 随当前源动态生成 |
| 空状态/错误 | 含 RQData 的固定文案 | 改为“当前数据源”或具体源名 |

保留 `RQData` 的位置仅限：

- 数据源下拉菜单；
- 来源详情浮层；
- 图表 footer 的来源标识；
- 数据源对照文档。

### 7.5 字段说明随源切换（推荐方案）

**问题**：每张图下的 `<details class="chart-source">` 目前硬编码 RQData 接口字段；切到 DDB 后如果继续显示 RQData 字段，会误导。

**推荐：后端驱动的字段说明（source-owned descriptions）**

每个 `DailySource` 自带结构化说明：

```python
{
  "chart": [
    {"label": "ETF 与指数分钟 K 线", "code": "LiveMarketDataClient · bar_", "note": "..."}
  ],
  "chartCum": [...],
  "chartBroad": [...],
  "chartDAmt": [...],
  "chartDNf": [...]
}
```

`GET /api/sources` 中每个日频源的 `descriptions` 结构：

```json
{
  "id": "ddb",
  "label": "DDB",
  "descriptions": {
    "chart": [
      {"label": "ETF 日收盘/成交额", "code": "mkt_fundd.CLOSE_PRICE / TURNOVER_VALUE", "note": "..."}
    ],
    "chartBroad": [
      {"label": "ETF 份额与净值", "code": "fund_etf_nr.SHARES / NAV", "note": "..."}
    ]
  }
}
```

同一响应顶层还包含：

```json
{
  "default": "rqdata",
  "realtime_default": "rqdata",
  "sources": ["..."],
  "realtime_sources": [
    {"id": "rqdata", "label": "RQData", "ready": true, "capabilities": ["bars", "tick", "index"]}
  ]
}
```

前端只负责渲染：

```js
function renderSourceNotes(source) {
  document.querySelectorAll('.chart-source').forEach(box => {
    const key = box.dataset.sourceFor;
    const items = source.descriptions?.[key];
    if (!items) { box.hidden = true; return; }
    box.hidden = false;
    box.querySelector('.source-items').innerHTML = items.map(renderItem).join('');
  });
}
```

优点：

- 字段说明由各数据源适配器维护，新增源只需后端提供描述；
- 不会出现“DDB 数据 + RQData 字段”的错配；
- 图表 key 与现有 `data-source-for` 一致，改动小；
- 描述可包含 `capabilities`，缺失能力时不显示对应图表说明。

备选方案：

- 前端 `source-notes.js` 静态映射：实现快，但两套字段容易漂移；
- 图表不显示字段说明：最简，但用户无法核对口径。

**建议采用后端驱动方案**，前端只做渲染和切换。

切换时的顺序：

```text
1. 请求 /api/board?source=ddb
2. 成功后一次性更新：
   - seed / charts
   - 图表 footer 来源
   - 所有 .chart-source 字段说明
   - 页头来源标识
3. 任一步失败：不替换，保留旧源，避免半套 UI
```

### 7.6 来源标识

| 位置 | 内容 |
|---|---|
| 页头 | `日频：RQData` 轻量徽标 |
| 页头实时 | `实时：RQData` 更小的灰色徽标 |
| 每张日频图表 footer | `日频源：DDB · 2026-11-17` |
| 图表说明 summary | `数据源与字段 · DDB` |
| 来源详情 | 能力、最新日期、构建时间、缺失项 |

颜色：

- RQData：青绿 `#087f73`
- DDB：靛蓝 `#4c6fff`
- 未就绪：琥珀 `#d69b29`
- 错误：红色 `#ce4657`

页头不要同时出现两个大色块；日频源用主色，实时源用中性灰。

### 7.7 来源详情浮层

由下拉菜单底部“数据源说明…”打开：

```text
┌──────────────────────────────────────────────┐
│ 数据源状态                                 × │
├──────────────────────────────────────────────┤
│ RQData   日频 + 实时   ● 已就绪              │
│ 数据日期 2026-11-17                          │
│ 构建时间 2026-11-18 09:20                    │
│ 能力：行情/成交额/份额/净值/拆分/日历        │
├──────────────────────────────────────────────┤
│ DDB      日频          ● 未就绪              │
│ 行情日期 2026-11-17                          │
│ 份额日期 2026-07-14  ← 阻断净流入/规模       │
│ 缺失：份额、净申赎、规模                     │
│ 说明：fund_etf_nr 未更新                     │
└──────────────────────────────────────────────┘
```

- 不暴露 DDB 账号/密码；
- 未就绪原因具体到表/日期；
- 底部链接到数据源对照文档。

### 7.8 切换状态

| 状态 | UI |
|---|---|
| 正常 | 下拉按钮显示当前源 + 状态点 |
| 切换中 | 按钮 loading；图表上方轻量遮罩“正在切换到 DDB…” |
| 目标未就绪 | 菜单项置灰；副文案“份额 2026-07-14 · 暂不可选” |
| 切换失败 | 保留原源；红色 toast“DDB 切换失败：…；已保留 RQData” |
| URL 强制不可用源 | 错误 banner；回退到默认源并同步 URL |

### 7.9 视觉示意

设计效果图：[data-source-switch-v3.png](mockups/data-source-switch-v3.png)

示意重点：

- 页头不再出现 RQData 品牌，只保留 `ETF FLOW / ETF 资金观测台`；
- 右上角同时有“日频数据源”和“实时行情源”两个下拉；
- 日频下拉展开后含状态、日期、未就绪原因；
- 日频与实时来源标识分开但轻量；
- 图表卡片显示当前日频源；
- 字段说明使用“数据源与字段”，随源切换。

## 8. 定时更新与部署

| 时间 | 任务 |
|---|---|
| 08:20 | 构建 RQData + DDB 日频快照（`--recent`） |
| 09:20 | 盘前补采（重点检查 DDB `fund_etf_nr` 是否已更新） |
| 12:30 | 两源补采 + crowding |
| 15:10 | `live --close`（仅 RQData 实时缓存） |

- DDB 首次全量构建一次，之后增量；
- DDB 构建失败不影响 RQData seed；
- 两个源各自记录 `built_at`；
- `fund_etf_nr` 恢复更新后，DDB 才允许 `ready=true`。

## 9. 测试与验收

| 测试 | 内容 |
|---|---|
| 适配器契约 | RQData/DDB 输出统一 `DailyBundle`，字段完整 |
| 一致性 | 重叠期 20 只核心 shares/nav/flow/close/amount/volume |
| 就绪检查 | `fund_etf_nr` 旧日期时 DDB ready=false |
| API | `/api/sources`、`?source=`、409 错误、X-Data-Source |
| 切换 | URL/localStorage 优先级；失败回退；不混合 |
| UI | 徽标、浮层、键盘、移动端 |
| 回归 | 原有 RQData 图表/净申赎/雷达不变 |

验收标准：

1. 默认打开为 RQData，页头显示 `日频：RQData`；
2. 点击 DDB（ready 时）无需刷新页面即可切换，所有日频指标切到 DDB；
3. DDB 未就绪时按钮禁用且原因可见；
4. 任一时刻，页面不会同时用两个日频源计算同一指标；
5. 实时区域始终标注 RQData。

## 10. 分阶段实施

| 阶段 | 内容 |
|---|---|
| P1 | 统一 `DailyBundle`、RQDataAdapter 包装、回归测试 |
| P2 | DDBAdapter、DDB seed 构建、readiness/一致性测试 |
| P3 | `/api/sources`、多 seed、source 参数 |
| P4 | UI 分段控件、徽标、来源浮层、切换状态 |
| P5 | 双源定时任务、文档、灰度；确认 DDB ETL 后切默认 |

## 11. 风险

| 风险 | 应对 |
|---|---|
| DDB `fund_etf_nr` 再次停更 | readiness 拒绝；DDB 按钮置灰 |
| DDB `fund_div` 拆分比例差异 | 断言 NET_INFLOW；差异超阈值拒绝构建 |
| 两源日期口径不同 | 统一到 `REG_DATE`/`EFFECT_DATE` 映射 |
| 混合实时/日频误解 | 两个独立徽标，文档说明 |
| 切换时图表闪动 | 保留旧图直到新 seed 到达，再原子替换 |
| Node/Cloudflare 遗留 | 明确只支持 RQData，文档标注 |

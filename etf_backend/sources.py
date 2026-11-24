"""Daily source adapters and source metadata.

RQData remains the default. DDB is read through DolphinDB `hermes_etf_data`
and is only considered ready when `fund_etf_nr` covers every core ETF up to
the requested flow date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Protocol

from . import provider
from .model import BASKET, INDEX, before, fund_flows, finite

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config/data-sources.json"


@dataclass
class SourceStatus:
    id: str
    label: str
    ready: bool
    latest_market_date: str | None = None
    latest_flow_date: str | None = None
    reason: str | None = None
    capabilities: list[str] | None = None


class DailySource(Protocol):
    id: str
    label: str

    def status(self, target: str) -> SourceStatus:
        ...

    def target_date(self, cutoff: str) -> str:
        ...

    def collect_history(self, start: str, end: str, minute_days: int = 0):
        ...


def dot(day: str) -> str:
    return day.replace("-", ".")


def load_config(path=None) -> dict:
    config_path = Path(path or os.environ.get("FTSHARE_SOURCE_CONFIG", DEFAULT_CONFIG))
    return json.loads(config_path.read_text(encoding="utf-8"))


def source_entries(config: dict) -> list[dict]:
    return config.get("sources", [])


def source_entry(config: dict, source_id: str) -> dict:
    for item in source_entries(config):
        if item.get("id") == source_id:
            return item
    raise KeyError(f"Unknown daily source: {source_id}")


def source_capabilities(config: dict, source_id: str) -> list[str]:
    return list(source_entry(config, source_id).get("capabilities", []))


def realtime_entries(config: dict) -> list[dict]:
    return config.get("realtime_sources", [])


def source_seed_path(config: dict, source_id: str) -> Path:
    return ROOT / source_entry(config, source_id)["seed"]


def source_descriptions(source_id: str) -> dict:
    """Field notes rendered under each chart. Kept next to the adapter."""
    common = {
        "chart": [
            {"label": "ETF 与指数分钟 K 线", "code": "RQData LiveMarketDataClient · tick_ 聚合 / bar_ 对账 / get_price(1m)",
             "note": "默认 bar 正式确认、Tick 并行验证及分钟内候选；可配置 Tick 分钟驱动，缺口用分钟线补齐。"},
            {"label": "指数上一交易日收盘", "code": "RQData get_price(1d)", "note": "取不复权 close。"},
        ],
        "chartCum": [
            {"label": "当日、历史与近20日分钟累计", "code": "RQData get_price(1m)",
             "note": "使用 total_turnover，按 240 个分钟点对齐。"},
        ],
        "chartTypCase": [
            {"label": "盘中与历史分钟成交额", "code": "RQData LiveMarketDataClient / get_price(1m)",
             "note": "使用 total_turnover。"},
        ],
        "chartTypNorm": [
            {"label": "常态日分钟成交额", "code": "RQData get_price(1m)", "note": "使用 total_turnover。"},
        ],
        "chartBroad": [
            {"label": "ETF 份额与单位净值", "code": "RQData fund.get_daily_units / fund.get_nav",
             "note": "日度份额与单位净值；缺失时保留缺口。"},
            {"label": "折溢价", "code": "RQData get_price(1d, adjust_type='none') / fund.get_nav",
             "note": "使用不复权收盘价与同日单位净值。"},
        ],
        "chartDAmt": [
            {"label": "历史与当日成交额", "code": "RQData get_price(1d)",
             "note": "使用 total_turnover。"},
        ],
        "chartDNf": [
            {"label": "历史净申赎", "code": "RQData fund.get_daily_units / fund.get_nav / fund.get_split",
             "note": "按现有净申赎公式计算。"},
        ],
        "chartAmt": [
            {"label": "核心 ETF 日成交额", "code": "RQData get_price(1d)",
             "note": "使用 total_turnover。"},
        ],
        "chartNf": [
            {"label": "核心 ETF 日净申赎", "code": "RQData fund.get_daily_units / fund.get_nav / fund.get_split",
             "note": "按现有净申赎公式计算。"},
        ],
        "etfTable": [
            {"label": "核心 ETF 实时表", "code": "RQData LiveMarketDataClient · tick_",
             "note": "最新价、累计成交额、盘口与行情时间；IOPV 溢价时效待核，仅作参考，历史回放可无盘口。"},
        ],
        "broadTable": [
            {"label": "宽基成交额与指数", "code": "RQData get_price(1d) / index_components",
             "note": "按宽基分类聚合。"},
        ],
    }
    if source_id == "rqdata":
        return common
    if source_id != "ddb":
        return {}
    ddb = {
        "chart": [
            {"label": "ETF 与指数分钟 K 线", "code": "RQData LiveMarketDataClient · bar_ / get_price(1m)",
             "note": "分钟行情来自 RQData，不随日频源切换。"},
            {"label": "指数上一交易日收盘", "code": "RQData get_price(1d)", "note": "盘中缓存使用 RQData 昨收。"},
        ],
        "chartCum": [
            {"label": "分钟累计", "code": "RQData get_price(1m)（实时/历史分钟共用）",
             "note": "DDB 本期不提供分钟历史，分钟图仍使用 RQData 分钟归档。"},
        ],
        "chartTypCase": [
            {"label": "盘中与历史分钟成交额", "code": "RQData LiveMarketDataClient / get_price(1m)",
             "note": "分钟数据来自实时源，不随日频源切换。"},
        ],
        "chartTypNorm": [
            {"label": "常态日分钟成交额", "code": "RQData get_price(1m)",
             "note": "分钟数据来自 RQData 归档。"},
        ],
        "chartBroad": [
            {"label": "ETF 份额与净值", "code": "fund_etf_nr.SHARES / NAV",
             "note": "DDB 份额表；需保持更新。"},
            {"label": "折溢价", "code": "mkt_fundd.CLOSE_PRICE / (fund_etf_nr.NAV 或 fund_nav.NAV)",
             "note": "优先份额表净值，缺失时按同代码、同归属日回退单位净值。"},
        ],
        "chartDAmt": [
            {"label": "历史与当日成交额", "code": "mkt_fundd.TURNOVER_VALUE",
             "note": "DDB 日成交额，单位元。"},
        ],
        "chartDNf": [
            {"label": "历史净申赎", "code": "fund_etf_nr.SHARES / NAV / SPLIT_RATIO",
             "note": "按现有净申赎公式计算。"},
        ],
        "chartAmt": [
            {"label": "核心 ETF 日成交额", "code": "mkt_fundd.TURNOVER_VALUE",
             "note": "DDB 日成交额，单位元。"},
        ],
        "chartNf": [
            {"label": "核心 ETF 日净申赎", "code": "fund_etf_nr.SHARES / NAV / SPLIT_RATIO → fund_flows",
             "note": "用共同公式及 SPLIT_RATIO 累乘因子计算，与 NET_INFLOW 对账；不可验证的折算拒绝构建。"},
        ],
        "etfTable": [
            *common["etfTable"],
        ],
        "broadTable": [
            {"label": "宽基成交额与指数", "code": "mkt_fundd / AIndexEODPrices",
             "note": "按宽基分类聚合。"},
        ],
    }
    return ddb


class RQDataAdapter:
    """Thin adapter over the existing RQData provider helpers."""

    id = "rqdata"
    label = "RQData"
    snapshot_minute_days = None

    def __init__(self, api, config: dict, universe: dict | None = None):
        self.api = api
        self.config = config
        self.universe = universe or json.loads((ROOT / "config/universe.json").read_text(encoding="utf-8"))

    def status(self, target: str) -> SourceStatus:
        # The file-level/dynamic readiness check lives in web.py; collection
        # itself only needs the provider to be available.
        return SourceStatus(self.id, self.label, True)

    def target_date(self, cutoff: str) -> str:
        dates = self.api.get_trading_dates(before(cutoff, 40), cutoff)
        if not len(dates):
            raise ValueError("No trading date before daily cutoff")
        return str(dates[-1])[:10]

    def collect_history(self, start: str, end: str, minute_days: int = 0):
        market = provider.collect(self.api, {
            "kind": "history", "symbols": BASKET + [INDEX], "start": start, "end": end,
            "market_start": self.config["market_start"], "minute_days": minute_days,
        })
        funds = provider.fund_history([fund["symbol"] for fund in self.universe["funds"]], start, end)["funds"]
        close_end = min(end, before(datetime.now(provider.CHINA).date().isoformat(), 1))
        closes = provider.traded_closes(self.api, list(funds), self.config["flow_start"], close_end)
        return market, {"funds": funds}, closes


class DDBAdapter:
    id = "ddb"
    label = "DDB"
    snapshot_minute_days = 0

    def __init__(self, config=None):
        self.config = config or load_config()
        self.host = os.environ.get("DDB_HOST", "10.72.236.101")
        self.port = int(os.environ.get("DDB_PORT", "8963"))
        self.user = os.environ.get("DDB_USER", "wuzy")
        self.password = os.environ.get("DDB_PASSWORD")
        self._session = None

    def session(self):
        if self._session is None:
            if not self.password:
                raise RuntimeError("DDB_PASSWORD is not set")
            import dolphindb as ddb
            session = ddb.session()
            session.connect(self.host, self.port, self.user, self.password)
            self._session = session
        return self._session

    def query(self, sql):
        return self.session().run(sql)

    def _core(self):
        basket = json.loads((ROOT / "config/basket.json").read_text(encoding="utf-8"))["symbols"]
        return [symbol.split(".")[0] for symbol in basket]

    def target_date(self, cutoff: str) -> str:
        # Observed index sessions avoid requesting Sunday/holiday fund data.
        frame = self.query("select max(TRADE_DT) as maxd from "
                           "loadTable('dfs://WIND_AIndexEODPrices','AIndexEODPrices') "
                           "where S_INFO_WINDCODE='000001.SH' and TRADE_DT between "
                           f"{dot(before(cutoff, 40))} : {dot(cutoff)}")
        if not len(frame) or str(frame.iloc[0]["maxd"]) in {"NaT", "None", "nan"}:
            raise ValueError("DDB index calendar unavailable")
        return str(frame.iloc[0]["maxd"])[:10]

    def status(self, target: str) -> SourceStatus:
        core = self._core()
        try:
            frame = self.query(
                "select TICKER_SYMBOL,max(TRADE_DATE) as maxd "
                "from loadTable('dfs://hermes_etf_data','fund_etf_nr') "
                f"where TICKER_SYMBOL in {core!r} and TRADE_DATE={dot(target)} "
                "and SHARES>0 and NAV>0 group by TICKER_SYMBOL")
            per_symbol = {str(row["TICKER_SYMBOL"]): str(row["maxd"])[:10]
                          for _, row in frame.iterrows()} if len(frame) else {}
            market = self.query(
                "select TICKER_SYMBOL,max(TRADE_DATE) as maxd from loadTable('dfs://hermes_etf_data','mkt_fundd') "
                f"where TICKER_SYMBOL in {core!r} and TRADE_DATE={dot(target)} "
                "and CLOSE_PRICE>0 and TURNOVER_VALUE>=0 group by TICKER_SYMBOL")
            market_dates = {str(row["TICKER_SYMBOL"]): str(row["maxd"])[:10]
                            for row in market.to_dict("records")}
            market_latest = min(market_dates.values()) if market_dates else None
            target_rows = self.query(
                "select TICKER_SYMBOL,SHARES,NAV from loadTable('dfs://hermes_etf_data','fund_etf_nr') "
                f"where TRADE_DATE={dot(target)} and TICKER_SYMBOL in {core!r}")
            target_values = {str(row["TICKER_SYMBOL"]): (row["SHARES"], row["NAV"])
                             for _, row in target_rows.iterrows()} if len(target_rows) else {}
        except Exception as error:
            return SourceStatus(self.id, self.label, False, reason=f"DDB 查询失败：{type(error).__name__}")
        missing = [symbol for symbol in core if symbol not in per_symbol]
        stale = [symbol for symbol in core if symbol in per_symbol and per_symbol[symbol] < target]
        def positive(value):
            try:
                number = float(value)
                return number == number and number > 0
            except (TypeError, ValueError):
                return False
        invalid = [symbol for symbol in core
                   if symbol not in target_values
                   or not positive(target_values[symbol][0]) or not positive(target_values[symbol][1])]
        latest = min(per_symbol.values()) if per_symbol else None
        if missing:
            return SourceStatus(self.id, self.label, False, latest_market_date=market_latest,
                                latest_flow_date=latest,
                                reason=f"fund_etf_nr 缺少核心 ETF：{','.join(missing[:5])}")
        if stale:
            return SourceStatus(self.id, self.label, False, latest_market_date=market_latest,
                                latest_flow_date=latest,
                                reason=f"fund_etf_nr 有 {len(stale)} 只核心 ETF 未到 {target}")
        if invalid:
            return SourceStatus(self.id, self.label, False, latest_market_date=market_latest,
                                latest_flow_date=latest,
                                reason=f"fund_etf_nr 有 {len(invalid)} 只核心 ETF 份额/净值无效")
        missing_market = [symbol for symbol in core if market_dates.get(symbol, "") < target]
        if missing_market:
            return SourceStatus(self.id, self.label, False, latest_market_date=market_latest,
                                latest_flow_date=latest,
                                reason=f"mkt_fundd 有 {len(missing_market)} 只核心 ETF 缺少 {target} 有效行情")
        return SourceStatus(self.id, self.label, True, latest_market_date=market_latest,
                            latest_flow_date=latest)

    def collect_history(self, start: str, end: str, minute_days: int = 0):
        """Return the same shape as provider.collect(history) plus fund history."""
        universe = json.loads((ROOT / "config/universe.json").read_text(encoding="utf-8"))
        symbols = [fund["symbol"] for fund in universe["funds"]]
        bare = [symbol.split(".")[0] for symbol in symbols]
        info = self._query_batches(
            "select SECURITY_ID,TICKER_SYMBOL from loadTable('dfs://hermes_etf_data','fund_etf_info') "
            "where TICKER_SYMBOL in {values}", bare)
        tickers = set(info["TICKER_SYMBOL"].tolist()) if len(info) else set()
        symbols = [symbol for symbol in symbols if symbol.split(".")[0] in tickers]
        bare = [symbol.split(".")[0] for symbol in symbols]
        bars = self._query_batches(
            "select TICKER_SYMBOL,TRADE_DATE,CLOSE_PRICE,TURNOVER_VOL,TURNOVER_VALUE "
            "from loadTable('dfs://hermes_etf_data','mkt_fundd') "
            "where TICKER_SYMBOL in {values} and TRADE_DATE between " + dot(start) + " : " + dot(end), bare)
        shares = self._query_batches(
            "select TICKER_SYMBOL,TRADE_DATE,SHARES,NAV,SPLIT_RATIO,NET_INFLOW "
            "from loadTable('dfs://hermes_etf_data','fund_etf_nr') "
            "where TICKER_SYMBOL in {values} and TRADE_DATE between " + dot(start) + " : " + dot(end), bare)
        index = self.query(
            "select TRADE_DT,S_DQ_CLOSE from loadTable('dfs://WIND_AIndexEODPrices','AIndexEODPrices') "
            f"where S_INFO_WINDCODE='000001.SH' and TRADE_DT between {dot(start)} : {dot(end)}")
        import pandas as pd
        for frame in (bars, shares, index):
            for column in ("TRADE_DATE", "TRADE_DT"):
                if column in frame.columns:
                    frame[column] = pd.to_datetime(frame[column]).dt.strftime("%Y-%m-%d")
        for frame, keys in ((bars, ["TICKER_SYMBOL", "TRADE_DATE"]),
                            (shares, ["TICKER_SYMBOL", "TRADE_DATE"]), (index, ["TRADE_DT"])):
            if len(frame) and frame.duplicated(keys).any():
                raise ValueError("Duplicate DDB daily rows")
        by_symbol = {symbol: symbol.split(".")[0] for symbol in symbols}
        full = {bare_value: symbol for symbol, bare_value in by_symbol.items()}
        daily, closes = [], {}
        core = set(self._core())
        for row in bars.to_dict("records"):
            code = str(row["TICKER_SYMBOL"])
            symbol = full.get(code)
            if not symbol:
                continue
            day = row["TRADE_DATE"]
            close = float(row["CLOSE_PRICE"]) if pd.notna(row["CLOSE_PRICE"]) else None
            if close is None:
                continue
            closes.setdefault(day, {})[symbol] = close
            if code in core:
                turnover = float(row["TURNOVER_VALUE"]) if pd.notna(row["TURNOVER_VALUE"]) else None
                stamp = int(datetime.fromisoformat(day + "T15:00:00+08:00").timestamp() * 1000)
                daily.append([symbol, stamp, close, turnover])
        for row in index.to_dict("records"):
            day = row["TRADE_DT"]
            close = float(row["S_DQ_CLOSE"]) if pd.notna(row["S_DQ_CLOSE"]) else None
            if close is not None:
                daily.append(["000001.SH", int(datetime.fromisoformat(day + "T15:00:00+08:00").timestamp() * 1000), close, None])
        fund_rows = {symbol: {"shares": [], "navs": [], "splits": [],
                              "unverified_split_dates": [],
                              "split_source": "DDB SPLIT_RATIO with NET_INFLOW reconciliation"} for symbol in symbols}
        factors, factor_dates, expected_flows = {}, {}, {}
        for row in shares.sort_values(["TICKER_SYMBOL", "TRADE_DATE"]).to_dict("records"):
            symbol = full.get(str(row["TICKER_SYMBOL"]))
            if not symbol:
                continue
            day = row["TRADE_DATE"]
            ratio = float(row["SPLIT_RATIO"]) if pd.notna(row["SPLIT_RATIO"]) else None
            expected = provider.number(row["NET_INFLOW"])
            if ratio is None and expected is None:
                fund_rows[symbol]["unverified_split_dates"].append(day)
            if ratio is not None and ratio <= 0:
                raise ValueError(f"Invalid DDB split ratio: {symbol} {day}")
            if ratio is not None and ratio != 1 and expected is None:
                raise ValueError(f"DDB split requires NET_INFLOW reconciliation: {symbol} {day}")
            factors[symbol] = factors.get(symbol, 1.0) * (ratio or 1.0)
            factor_dates[symbol, day] = factors[symbol]
            expected_flows[symbol, day] = expected
            if pd.notna(row["SHARES"]) and float(row["SHARES"]) > 0:
                fund_rows[symbol]["shares"].append([day, float(row["SHARES"])])
            if pd.notna(row["NAV"]) and float(row["NAV"]) > 0:
                fund_rows[symbol]["navs"].append([day, float(row["NAV"]), factors[symbol]])
            if pd.notna(row["SPLIT_RATIO"]) and float(row["SPLIT_RATIO"]) > 0:
                fund_rows[symbol]["splits"].append([day, float(row["SPLIT_RATIO"])])
        calendar = sorted(set(index["TRADE_DT"].tolist())) if len(index) else sorted(closes)
        # Only query the fallback table for products with missing unit NAVs.
        known_nav_dates = {symbol: {day for day, _, _ in fund_rows[symbol]["navs"]} for symbol in symbols}
        missing_nav = {symbol for symbol in symbols
                       if set(closes_day for closes_day, values in closes.items() if symbol in values)
                       - known_nav_dates[symbol]}
        if missing_nav:
            ids = info.loc[info["TICKER_SYMBOL"].isin([s[:6] for s in missing_nav]), "SECURITY_ID"].tolist()
            navs = self._query_batches(
                "select SECURITY_ID,END_DATE,NAV from loadTable('dfs://hermes_etf_data','fund_nav') "
                "where SECURITY_ID in {values} and END_DATE between " + dot(start) + " : " + dot(end), ids)
            if len(navs):
                if navs.duplicated(["SECURITY_ID", "END_DATE"]).any():
                    raise ValueError("Duplicate DDB NAV rows")
                navs = navs.merge(info, on="SECURITY_ID", validate="many_to_one")
                for row in navs.to_dict("records"):
                    symbol = full.get(str(row["TICKER_SYMBOL"]))
                    day = str(row["END_DATE"])[:10]
                    nav = provider.number(row["NAV"])
                    if day not in known_nav_dates[symbol] and nav and nav > 0:
                        factor = factor_dates.get((symbol, day))
                        fund_rows[symbol]["navs"].append([day, nav, factor])
                        known_nav_dates[symbol].add(day)
        for fund in universe["funds"]:
            symbol = fund["symbol"]
            if symbol not in fund_rows:
                continue
            computed = fund_flows(fund, fund_rows[symbol], calendar)
            for day, row in computed.items():
                expected = expected_flows.get((symbol, day))
                actual = row["flow"]
                if expected is not None and finite(actual) and abs(actual * 1e8 - expected) > max(.01, abs(expected) * 1e-9):
                    raise ValueError(f"DDB NET_INFLOW mismatch: {symbol} {day}")
            for day, ratio in fund_rows[symbol]["splits"]:
                if ratio != 1 and (day not in computed or computed[day]["flow"] is None):
                    raise ValueError(f"DDB split cannot be verified: {symbol} {day}")
        market = {"calendar": calendar, "daily": daily, "minutes": []}
        return market, {"funds": fund_rows}, closes

    def _query_batches(self, template: str, values: list[str], batch: int = 80):
        import pandas as pd
        frames = []
        for i in range(0, len(values), batch):
            chunk = values[i:i + batch]
            frames.append(self.query(template.format(values=chunk)))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_source(source_id: str, config: dict, api=None, universe: dict | None = None,
                 collection_config: dict | None = None) -> DailySource:
    if source_id == "rqdata":
        if api is None:
            raise ValueError("RQData API is required")
        return RQDataAdapter(api, collection_config or {}, universe)
    if source_id == "ddb":
        return DDBAdapter(config)
    raise KeyError(f"Unknown daily source: {source_id}")

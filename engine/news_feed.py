#!/usr/bin/env python3
"""
news_feed.py — headlines as trading REFERENCE (never an auto-signal).

Headlines are attached to the daily signal packet so the agent can read them
when writing its decision memo. They never feed the deterministic score.

Sources by runtime:
  * GitHub Actions (open internet): yfinance news, then Yahoo RSS fallback.
  * Claude cloud session: this module is skipped; the agent uses WebSearch.
  * Local run next to the Robinhood MCP: the agent can inject headlines via
    --inject-news (JSON file) — the slot below accepts any source.

Degrades to [] silently: a missing news source must never block a run.
"""
from __future__ import annotations

import datetime as dt
import json


def _from_yfinance(ticker: str, limit: int) -> list[dict]:
    import yfinance as yf

    items = yf.Ticker(ticker).news or []
    out = []
    for it in items[:limit]:
        content = it.get("content", it)  # yfinance >=0.2.5x nests under 'content'
        title = content.get("title")
        if not title:
            continue
        out.append({
            "ticker": ticker,
            "title": title,
            "publisher": (content.get("provider") or {}).get("displayName")
                         if isinstance(content.get("provider"), dict)
                         else content.get("publisher"),
            "published": content.get("pubDate") or content.get("providerPublishTime"),
            "link": (content.get("canonicalUrl") or {}).get("url")
                    if isinstance(content.get("canonicalUrl"), dict)
                    else content.get("link"),
        })
    return out


def _from_rss(ticker: str, limit: int) -> list[dict]:
    import feedparser

    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    feed = feedparser.parse(url)
    return [{"ticker": ticker, "title": e.get("title"),
             "publisher": "Yahoo RSS", "published": e.get("published"),
             "link": e.get("link")} for e in feed.entries[:limit]]


def get_headlines(tickers: list[str], limit_per_ticker: int = 5) -> list[dict]:
    all_items = []
    for t in tickers:
        for source in (_from_yfinance, _from_rss):
            try:
                items = source(t, limit_per_ticker)
                if items:
                    all_items.extend(items)
                    break
            except Exception:  # noqa: BLE001 — news must never block a run
                continue
    return all_items


def load_injected(path: str) -> list[dict]:
    """Load agent-injected headlines (e.g. gathered via Robinhood MCP or WebSearch)."""
    try:
        with open(path) as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except Exception:  # noqa: BLE001
        return []


if __name__ == "__main__":
    import sys
    print(json.dumps(get_headlines(sys.argv[1:] or ["SPY"]), indent=2, default=str))

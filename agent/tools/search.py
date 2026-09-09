"""
Search tools for weather, time, news, and general search.
Non-blocking execution using asyncio loop executors with bounded timeouts and input sanitization.
"""

import sys
import re
import urllib.parse
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
import zoneinfo
import requests
from bs4 import BeautifulSoup

from agent.utils.logger import get_logger

logger = get_logger(__name__)

# Bounded HTTP timeout for all external search calls
SEARCH_TIMEOUT_SECONDS = 4.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

COMMON_CITY_TIMEZONES = {
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "tokyo": "Asia/Tokyo",
    "singapore": "Asia/Singapore",
    "hong kong": "Asia/Hong_Kong",
    "sydney": "Australia/Sydney",
    "utc": "UTC",
    "gmt": "GMT",
}


async def _run_in_thread(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


# =============================================
# WEATHER (via wttr.in — sanitized & bounded)
# =============================================

def _get_weather_sync(city: str) -> Dict[str, Any]:
    clean_city = city.strip()
    if not clean_city:
        return {"error": "Please provide a valid city name"}

    encoded_city = urllib.parse.quote_plus(clean_city)
    url = f"https://wttr.in/{encoded_city}?format=j1"
    
    resp = requests.get(url, headers=HEADERS, timeout=SEARCH_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    current = data.get("current_condition", [{}])[0]
    location = data.get("nearest_area", [{}])[0]

    return {
        "location": location.get("areaName", [{"value": clean_city}])[0].get("value", clean_city),
        "country": location.get("country", [{"value": "Unknown"}])[0].get("value", ""),
        "temperature_c": current.get("temp_C", "N/A"),
        "temperature_f": current.get("temp_F", "N/A"),
        "condition": current.get("weatherDesc", [{"value": "Unknown"}])[0].get("value", ""),
        "humidity": current.get("humidity", "N/A"),
        "wind_speed_kmh": current.get("windSpeedKmph", "N/A"),
        "wind_dir": current.get("winddir16Point", "N/A"),
        "feels_like_c": current.get("FeelsLikeC", "N/A"),
        "source": "wttr.in",
    }


async def get_weather(city: str) -> Dict[str, Any]:
    """
    Get current weather for a city using wttr.in.
    Non-blocking: executed in a background thread executor.
    """
    if not city or not city.strip():
        return {"error": "Please provide a city name"}

    try:
        return await _run_in_thread(_get_weather_sync, city)
    except Exception as e:
        logger.warning(f"Weather fetch failed for '{city}': {e}")
        return {"error": f"Could not get weather: {str(e)}", "location": city.strip()}


# =============================================
# TIME / DATE (via zoneinfo stdlib primary)
# =============================================

def _get_time_sync(timezone_or_city: str = "America/New_York") -> Dict[str, Any]:
    query = timezone_or_city.strip()
    query_lower = query.lower()
    
    # Resolve known cities to standard IANA timezone
    iana_tz = COMMON_CITY_TIMEZONES.get(query_lower, query)
    
    dt = None
    tz_obj = None
    
    # Primary: Fast, local, zero-network zoneinfo lookup
    try:
        tz_obj = zoneinfo.ZoneInfo(iana_tz)
        dt = datetime.now(tz_obj)
    except Exception:
        pass

    # If IANA timezone was not found directly, try finding matching timezone key
    if dt is None:
        for known_city, tz_name in COMMON_CITY_TIMEZONES.items():
            if known_city in query_lower:
                try:
                    tz_obj = zoneinfo.ZoneInfo(tz_name)
                    dt = datetime.now(tz_obj)
                    iana_tz = tz_name
                    break
                except Exception:
                    pass

    # Fallback: worldtimeapi.org with short timeout
    if dt is None:
        try:
            url = f"http://worldtimeapi.org/api/timezone/{urllib.parse.quote(query)}"
            resp = requests.get(url, headers=HEADERS, timeout=SEARCH_TIMEOUT_SECONDS)
            if resp.ok:
                data = resp.json()
                dt = datetime.fromisoformat(data["datetime"].replace("Z", "+00:00"))
                iana_tz = data.get("timezone", query)
        except Exception:
            pass

    # Final fallback: Local system time
    if dt is None:
        dt = datetime.now()
        iana_tz = "Local"

    utc_offset = dt.strftime("%z") or "UTC"

    return {
        "timezone": iana_tz,
        "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "day_of_week": dt.strftime("%A"),
        "month": dt.strftime("%B"),
        "year": dt.year,
        "utc_offset": utc_offset,
        "source": "Python zoneinfo",
    }


async def get_time(timezone: str = "America/New_York") -> Dict[str, Any]:
    """
    Get current time and date for a timezone or city.
    Non-blocking: executed in a background thread executor.
    """
    try:
        return await _run_in_thread(_get_time_sync, timezone)
    except Exception as e:
        logger.warning(f"Time fetch failed for '{timezone}': {e}")
        return {
            "error": f"Could not get time: {str(e)}",
            "timezone": timezone,
        }


# =============================================
# STOCK NEWS (via yfinance primary + Google News fallback)
# =============================================

def _get_stock_news_sync(symbol: str, limit: int = 3) -> Dict[str, Any]:
    company_name = symbol.strip().upper()
    if not company_name:
        return {"error": "Please provide a valid ticker symbol"}

    articles = []

    # Primary: yfinance news lookup
    try:
        import yfinance as yf
        ticker = yf.Ticker(company_name)
        yf_news = getattr(ticker, "news", [])
        if yf_news:
            for item in yf_news[:limit]:
                content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
                title = content.get("title") or item.get("title")
                provider = content.get("provider", {}) if isinstance(content.get("provider"), dict) else {}
                source = provider.get("displayName") or item.get("publisher", "Financial News")
                click_url = content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else item.get("link")

                if title:
                    articles.append({
                        "title": title,
                        "source": source,
                        "published": item.get("providerPublishTime"),
                        "link": click_url,
                    })
    except Exception as e:
        logger.debug(f"yfinance news lookup fallback: {e}")

    # Fallback: Google News search with bounded timeout
    if not articles:
        try:
            query = f"{company_name} stock news"
            url = f"https://news.google.com/search?q={urllib.parse.quote_plus(query)}"
            resp = requests.get(url, headers=HEADERS, timeout=SEARCH_TIMEOUT_SECONDS)
            if resp.ok:
                soup = BeautifulSoup(resp.text, "html.parser")
                for item in soup.find_all("article")[:limit]:
                    try:
                        title_elem = item.find("a", class_="JtKRv") or item.find("a")
                        title = title_elem.get_text(strip=True) if title_elem else None
                        link = None
                        if title_elem and title_elem.get("href"):
                            href = title_elem["href"]
                            link = "https://news.google.com" + href[1:] if href.startswith(".") else href
                        source_elem = item.find("div", class_="vr1PYe") or item.find("span")
                        source = source_elem.get_text(strip=True) if source_elem else "Financial News"
                        if title:
                            articles.append({"title": title, "source": source, "published": None, "link": link})
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Google News fallback failed: {e}")

    if not articles:
        return {
            "symbol": company_name,
            "news": [],
            "note": "No recent news found for this ticker.",
            "source": "Stock News Service",
        }

    return {
        "symbol": company_name,
        "company": company_name,
        "news": articles,
        "count": len(articles),
        "source": "yfinance / News Service",
    }


async def get_stock_news(symbol: str, limit: int = 3) -> Dict[str, Any]:
    """
    Get latest news for a stock ticker.
    Non-blocking: executed in a background thread executor.
    """
    if not symbol or not symbol.strip():
        return {"error": "Please provide a ticker symbol"}

    try:
        return await _run_in_thread(_get_stock_news_sync, symbol, limit)
    except Exception as e:
        logger.warning(f"News fetch failed for '{symbol}': {e}")
        return {
            "error": f"Could not fetch news: {str(e)}",
            "symbol": symbol.strip().upper(),
        }


# =============================================
# GENERAL SEARCH (via DuckDuckGo Lite + Wikipedia fallback)
# =============================================

def _search_sync(query: str, limit: int = 5) -> Dict[str, Any]:
    clean_query = query.strip()
    if not clean_query:
        return {"error": "Please provide a search query"}

    results = []

    # Primary: DuckDuckGo Lite with bounded timeout
    try:
        resp = requests.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": clean_query},
            headers=HEADERS,
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        if resp.ok:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a.result-link")[:limit]:
                title = a.get_text(strip=True)
                link = a.get("href")
                if title:
                    results.append({"title": title, "description": "", "link": link})
    except Exception as e:
        logger.debug(f"DDG Lite search failed: {e}")

    # Fallback: Wikipedia Summary API
    if not results:
        try:
            wiki_term = urllib.parse.quote(clean_query.replace(" ", "_"))
            wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_term}"
            resp = requests.get(wiki_url, headers={"User-Agent": "DataForgeVoiceAgent/1.0"}, timeout=SEARCH_TIMEOUT_SECONDS)
            if resp.ok:
                data = resp.json()
                if data.get("title") and data.get("extract"):
                    results.append({
                        "title": data["title"],
                        "description": data["extract"],
                        "link": data.get("content_urls", {}).get("desktop", {}).get("page"),
                    })
        except Exception as e:
            logger.debug(f"Wikipedia search fallback failed: {e}")

    if not results:
        return {
            "query": clean_query,
            "results": [],
            "note": "No results found. Try a different query.",
            "source": "Search Service",
        }

    return {
        "query": clean_query,
        "results": results,
        "count": len(results),
        "source": "DuckDuckGo / Wikipedia",
    }


async def search(query: str, limit: int = 5) -> Dict[str, Any]:
    """
    General web search with bounded timeouts.
    Non-blocking: executed in a background thread executor.
    """
    if not query or not query.strip():
        return {"error": "Please provide a search query"}

    try:
        return await _run_in_thread(_search_sync, query, limit)
    except Exception as e:
        logger.warning(f"Search failed for '{query}': {e}")
        return {"error": f"Could not search: {str(e)}", "query": query.strip()}


async def route_search_request(intent: Dict[str, Any]) -> Dict[str, Any]:
    """Route search intent to appropriate tool."""
    search_type = intent.get("type", "general")
    query = intent.get("query", "")
    extra = intent.get("extra", {})

    if search_type == "weather":
        return await get_weather(query)

    if search_type == "time":
        tz = extra.get("timezone", "America/New_York")
        return await get_time(tz)

    if search_type == "news":
        symbol = query.upper()
        limit = extra.get("limit", 3)
        return await get_stock_news(symbol, limit)

    return await search(query)
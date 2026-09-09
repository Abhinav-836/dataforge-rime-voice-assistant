"""
Unit tests for financial data tools in agent/tools/stocks.py.
"""

import pytest
from unittest.mock import patch, MagicMock
from agent.tools.stocks import (
    format_volume,
    _get_finnhub_quote,
    _fetch_quote_sync,
    get_stock_quote,
    compare_stocks,
)


def test_format_volume():
    assert format_volume(1_500_000_000) == "1.50 billion"
    assert format_volume(42_150_000) == "42.15 million"
    assert format_volume(12_400) == "12.4 thousand"
    assert format_volume(500) == "500"


def test_finnhub_quote_mock_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "c": 182.50,
        "d": 1.25,
        "dp": 0.69,
        "h": 184.00,
        "l": 181.10,
        "pc": 181.25,
    }

    with patch("requests.get", return_value=mock_resp):
        with patch("agent.tools.stocks.config.finnhub_api_key", "test_key"):
            quote = _get_finnhub_quote("AAPL")
            assert quote is not None
            assert quote["symbol"] == "AAPL"
            assert quote["current_price"] == 182.50
            assert quote["change"] == 1.25
            assert quote["percent_change"] == 0.69
            assert quote["source"] == "finnhub"
            assert quote["volume"] is None


def test_volume_request_triggers_yfinance_fallback():
    """Verify that field='volume' bypasses or augments Finnhub with yfinance volume."""
    mock_yf = {
        "symbol": "AAPL",
        "current_price": 182.50,
        "change": 1.25,
        "percent_change": 0.69,
        "day_high": 184.00,
        "day_low": 181.10,
        "prev_close": 181.25,
        "volume": 45000000,
        "source": "yfinance",
    }

    with patch("agent.tools.stocks._get_yfinance_quote", return_value=mock_yf):
        result = _fetch_quote_sync("AAPL", field="volume")
        assert result is not None
        assert result["field"] == "volume"
        assert result["volume"] == 45000000
        assert result["source"] == "yfinance"


def test_finnhub_failure_falls_back_to_yfinance():
    mock_yf = {
        "symbol": "MSFT",
        "current_price": 420.00,
        "change": 3.00,
        "percent_change": 0.72,
        "day_high": 422.00,
        "day_low": 418.00,
        "prev_close": 417.00,
        "volume": 20000000,
        "source": "yfinance",
    }

    with patch("agent.tools.stocks._get_finnhub_quote", return_value=None):
        with patch("agent.tools.stocks._get_yfinance_quote", return_value=mock_yf):
            result = _fetch_quote_sync("MSFT", field="price")
            assert result is not None
            assert result["symbol"] == "MSFT"
            assert result["current_price"] == 420.00
            assert result["source"] == "yfinance"


@pytest.mark.asyncio
async def test_get_stock_quote_spoken_formatting():
    mock_quote = {
        "symbol": "AAPL",
        "current_price": 180.00,
        "change": 2.00,
        "percent_change": 1.12,
        "day_high": 181.0,
        "day_low": 178.0,
        "prev_close": 178.0,
        "volume": 50000000,
        "source": "yfinance",
        "field": "price"
    }

    with patch("agent.tools.stocks._fetch_quote_sync", return_value=mock_quote):
        res = await get_stock_quote({"symbols": ["AAPL"], "field": "price"})
        assert "AAPL is at $180.00, up 1.12% today." in res["spoken_text"]


@pytest.mark.asyncio
async def test_compare_stocks_requires_two():
    res = await compare_stocks({"symbols": ["AAPL"]})
    assert "error" in res
    assert "at least two" in res["error"]

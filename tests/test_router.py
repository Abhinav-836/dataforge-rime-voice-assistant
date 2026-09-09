"""
Unit tests for intent router in agent/router.py.
"""

from agent.router import (
    _extract_symbols,
    _extract_field,
    _is_comparison_query,
    route_intent,
)


def test_extract_company_names():
    assert _extract_symbols("What is Apple's price?") == ["AAPL"]
    assert _extract_symbols("Check Tesla and Nvidia") == ["TSLA", "NVDA"]
    assert _extract_symbols("How is Microsoft doing today?") == ["MSFT"]
    assert _extract_symbols("Quote for Taiwan Semiconductor") == ["TSM"]
    assert _extract_symbols("Look up Google") == ["GOOGL"]
    assert _extract_symbols("What about Disney stock?") == ["DIS"]


def test_extract_prefixed_and_raw_tickers():
    assert _extract_symbols("Check INTC") == ["INTC"]
    assert _extract_symbols("Price of AMD") == ["AMD"]
    assert _extract_symbols("Check $PLTR") == ["PLTR"]
    assert _extract_symbols("What about NVDA") == ["NVDA"]


def test_stopwords_not_parsed_as_tickers():
    symbols = _extract_symbols("WHAT IS THE CURRENT WEATHER TODAY")
    assert symbols == []

    symbols2 = _extract_symbols("SHOW ME THE PRICE OF THE STOCK")
    assert symbols2 == []


def test_field_extraction():
    assert _extract_field("What is the volume on Apple?") == "volume"
    assert _extract_field("What is Apple's price?") == "price"
    assert _extract_field("Check trading volume for TSLA") == "volume"


def test_comparison_detection():
    assert _is_comparison_query("Compare Apple and Tesla") is True
    assert _is_comparison_query("Apple vs Nvidia") is True
    assert _is_comparison_query("What is the price of Apple?") is False


def test_route_intent_correction_bumping():
    current_intent = {"symbols": ["AAPL"], "field": "price"}

    # User corrects to Nvidia
    intent, is_new = route_intent("Actually, check Nvidia instead", current_intent)
    assert is_new is True
    assert intent["symbols"] == ["NVDA"]

    # User switches intent from price to volume
    intent2, is_new2 = route_intent("What is the volume on Apple?", current_intent)
    assert is_new2 is True
    assert intent2["field"] == "volume"

    # User makes comparison
    intent3, is_new3 = route_intent("Compare Apple and Tesla", current_intent)
    assert is_new3 is True
    assert intent3["is_comparison"] is True
    assert set(intent3["symbols"]) == {"AAPL", "TSLA"}
    assert intent3["tool"] == "compare_stocks"

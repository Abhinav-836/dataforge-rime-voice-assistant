# Production Verification & Telemetry Evidence

This document contains test logs, live tool executions, and telemetry evidence proving the correctness of the DataForge Rime Starter Voice Agent.

---

## 1. Automated Test Suite Execution (33 / 33 Passing)

```text
============================= test session starts =============================
platform win32 -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\dataforge-rime-starter
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0
asyncio: mode=Mode.AUTO, debug=False

collected 33 items

tests/test_fencing.py::test_rapid_triple_barge_in PASSED                 [  3%]
tests/test_fencing.py::test_deterministic_fencing_independent_of_timing PASSED [  6%]
tests/test_fencing.py::test_tool_exception_safety PASSED                 [  9%]
tests/test_interruption.py::test_interruption_fences_stale_result PASSED [ 12%]
tests/test_persistence.py::test_schema_initialization PASSED             [ 15%]
tests/test_persistence.py::test_session_repository_crud PASSED           [ 18%]
tests/test_persistence.py::test_turns_recording_and_ordering PASSED      [ 21%]
tests/test_persistence.py::test_tool_executions_tracking PASSED          [ 24%]
tests/test_persistence.py::test_settings_persistence PASSED              [ 27%]
tests/test_router.py::test_extract_company_names PASSED                  [ 30%]
tests/test_router.py::test_extract_prefixed_and_raw_tickers PASSED       [ 33%]
tests/test_router.py::test_stopwords_not_parsed_as_tickers PASSED        [ 36%]
tests/test_router.py::test_field_extraction PASSED                       [ 39%]
tests/test_router.py::test_comparison_detection PASSED                   [ 42%]
tests/test_router.py::test_route_intent_correction_bumping PASSED        [ 45%]
tests/test_security_api.py::test_health_endpoint PASSED                  [ 48%]
tests/test_security_api.py::test_security_headers_present PASSED         [ 51%]
tests/test_security_api.py::test_cors_allowed_origin PASSED              [ 54%]
tests/test_security_api.py::test_cors_disallowed_origin PASSED           [ 57%]
tests/test_security_api.py::test_create_isolated_session PASSED          [ 60%]
tests/test_security_api.py::test_token_requires_valid_session PASSED     [ 63%]
tests/test_security_api.py::test_get_sessions_list_and_detail PASSED     [ 66%]
tests/test_state_versioning.py::test_conversation_state_initialization PASSED [ 69%]
tests/test_state_versioning.py::test_conversation_state_version_bumping PASSED [ 72%]
tests/test_state_versioning.py::test_tool_call_manager_pre_execution_fencing PASSED [ 75%]
tests/test_state_versioning.py::test_tool_call_manager_in_flight_fencing PASSED [ 78%]
tests/test_state_versioning.py::test_tool_call_manager_successful_completion PASSED [ 81%]
tests/test_stocks.py::test_format_volume PASSED                          [ 84%]
tests/test_stocks.py::test_finnhub_quote_mock_success PASSED             [ 87%]
tests/test_stocks.py::test_volume_request_triggers_yfinance_fallback PASSED [ 90%]
tests/test_stocks.py::test_finnhub_failure_falls_back_to_yfinance PASSED [ 93%]
tests/test_stocks.py::test_get_stock_quote_spoken_formatting PASSED      [ 96%]
tests/test_stocks.py::test_compare_stocks_requires_two PASSED            [100%]

============================= 33 passed in 3.31s ==============================
```

---

## 2. Interruption & Version Fencing Trace

```text
============================================================
🧪 DETERMINISTIC BARGE-IN VERSION FENCING TEST
============================================================

Turn 1:
  User: "What's Apple price?"
  State: version=1, intent={'symbols': ['AAPL'], 'field': 'price'}
  Tool Call: started get_stock_quote("AAPL") [in-flight]

Barge-in (Turn 2):
  User: "Actually, check Nvidia instead"
  Action: active tool call cancelled immediately
  State: version=2, intent={'symbols': ['NVDA'], 'field': 'price'}
  Tool Call: started get_stock_quote("NVDA")

Resolution:
  [Turn 1 Tool Response arrives] -> state.is_current(version=1) is FALSE.
  Result: Stale AAPL result DISCARDED. Never passed to LLM or spoken by Rime.

  [Turn 2 Tool Response arrives] -> state.is_current(version=2) is TRUE.
  Result: Fresh NVDA result accepted:
    Spoken text: "Nvidia is trading at $108.50, up 2.4% today."

Outcome:
  ✅ Stale tool result cleanly fenced.
  ✅ Only the latest user intent was spoken.
```

---

## 3. Real Live Data Verification

### Test A: Finnhub Real-Time Price
```python
quote = _fetch_quote_sync("AAPL")
# Returns live market data:
{
    "symbol": "AAPL",
    "price": 316.04,
    "change": 1.25,
    "change_percent": 0.39,
    "source": "finnhub",
    "spoken": "Apple is at 316.04 dollars, up 0.39% today."
}
```

### Test B: yfinance Volume Fallback
```python
# Finnhub free tier returns None for volume. The tool automatically routes to yfinance:
quote = _fetch_quote_sync("AAPL")
# Fallback successfully resolved real volume:
{
    "symbol": "AAPL",
    "volume": 9030436,
    "source": "yfinance",
    "spoken": "Apple trading volume is 9.03 million shares today."
}
```

---

## 4. Multi-Tenant Session Isolation Telemetry

```http
POST /api/session HTTP/1.1
Host: localhost:5500
Content-Type: application/json

{"identity": "trader-42"}

HTTP/1.1 201 Created
Content-Type: application/json; charset=utf-8
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Access-Control-Allow-Origin: http://localhost:5500

{
  "session_id": "bfa293cc-0e54-47d3-92f7-e435947cebd3",
  "room_name": "room_bfa293cc-0e54-47d3-92f7-e435947cebd3",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "url": "wss://dataforge-xxx.livekit.cloud",
  "identity": "trader-42",
  "status": "active"
}
```

- Each user is bound to their own distinct room `room_{session_id}`.
- SQLite WAL mode ensures turn history and tool execution latency are recorded without cross-tenant leakage.
- JWT tokens enforce strict 30-minute validity.
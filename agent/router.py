"""
Intent routing for the stock-quote and assistant product.

Handles:
  1. Single ticker: "What's Apple price?" or "Check INTC"
  2. Ticker switch: "Apple — no, Tesla"
  3. Multi-ticker comparison: "Compare Apple and Tesla"
  4. Intent switch: price -> volume
  5. Search queries: weather, time, news, general info

This is a fast heuristic router (regex/keyword) designed for deterministic,
low-latency turn versioning and fencing.
"""

import re
from typing import Tuple, List, Dict, Any, Set

# Comprehensive Company name -> ticker mapping (top 50+ global companies)
COMPANY_TO_TICKER = {
    # Big Tech
    "apple": "AAPL",
    "tesla": "TSLA",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "nvidia": "NVDA",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "intel": "INTC",
    "broadcom": "AVGO",
    "qualcomm": "QCOM",
    "cisco": "CSCO",
    "oracle": "ORCL",
    "ibm": "IBM",
    "adobe": "ADBE",
    "salesforce": "CRM",
    "palantir": "PLTR",
    "snowflake": "SNOW",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "asml": "ASML",
    "sony": "SONY",

    # Financial Services
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "j.p. morgan": "JPM",
    "chase": "JPM",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "goldman sachs": "GS",
    "goldman": "GS",
    "morgan stanley": "MS",
    "visa": "V",
    "mastercard": "MA",
    "paypal": "PYPL",
    "coinbase": "COIN",
    "berkshire": "BRK-B",
    "berkshire hathaway": "BRK-B",
    "lincoln national": "LNC",

    # Consumer & Retail
    "walmart": "WMT",
    "costco": "COST",
    "target": "TGT",
    "home depot": "HD",
    "nike": "NKE",
    "disney": "DIS",
    "walt disney": "DIS",
    "starbucks": "SBUX",
    "mcdonalds": "MCD",
    "mcdonald's": "MCD",
    "coca cola": "KO",
    "coca-cola": "KO",
    "coke": "KO",
    "pepsi": "PEP",
    "pepsico": "PEP",
    "electronic arts": "EA",

    # Transportation & Travel
    "uber": "UBER",
    "lyft": "LYFT",
    "airbnb": "ABNB",
    "spotify": "SPOT",

    # Healthcare & Energy
    "pfizer": "PFE",
    "moderna": "MRNA",
    "eli lilly": "LLY",
    "lilly": "LLY",
    "johnson & johnson": "JNJ",
    "j&j": "JNJ",
    "exxon": "XOM",
    "exxonmobil": "XOM",
    "chevron": "CVX",

    # Meme / Retail Favorites & International
    "amc": "AMC",
    "gamestop": "GME",
    "alibaba": "BABA",
    "baidu": "BIDU",
}

# Set of all known tickers from the company mapping plus common major tickers
KNOWN_TICKERS: Set[str] = set(COMPANY_TO_TICKER.values()) | {
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "SMH", "SOXX", "XLF", "XLE",
}

# Correction markers that indicate a new intent
CORRECTION_MARKERS = [
    "actually", "wait", "no", "instead",
    "change", "not", "make it", "just do", "i meant",
    "sorry", "cancel that", "scratch that",
]
_CORRECTION_PATTERNS = [
    re.compile(r"\b" + re.escape(m) + r"\b", re.IGNORECASE) for m in CORRECTION_MARKERS
]

# Comparison conjunctions
COMPARISON_CONJUNCTIONS = [
    " and ", " vs ", " versus ", " compare ",
    " & ", " , ", " ; ",
]

# Raw ticker pattern (uppercase 2-5 letters)
TICKER_PATTERN = re.compile(r"\b[A-Z]{2,5}\b")

# Prefixed ticker pattern (e.g. $AAPL, $tsla, stock AAPL, check INTC, price of amd)
PREFIXED_TICKER_PATTERN = re.compile(
    r"(?:\$|(?:\b(?:stock|ticker|shares?|price of|quote for|check|buy|sell|track|on|about)\s+))([A-Za-z]{1,5})\b",
    re.IGNORECASE,
)

# Words that look like tickers in all-caps text but are standard conversational words.
# This set must be comprehensive — anything missing here becomes a false-positive ticker.
COMMON_WORDS_NOT_TICKERS = {
    # Interrogatives & pronouns
    "WHAT", "WHEN", "WHERE", "WHICH", "WHO", "WHOM", "WHY", "HOW",
    "THE", "AND", "FOR", "NOT", "BUT", "ALL", "ANY", "ARE", "CAN",
    "IS", "AM", "BE", "SO", "IF", "AS", "AT", "BY", "IN", "ON", "TO",
    "UP", "AN", "IT", "OF", "DO", "DOES", "DID", "GET", "GOT", "HAD",
    "HAS", "HAVE", "HER", "HERS", "HE", "HIM", "HIS", "ITS", "NOW",
    "ONE", "OUR", "OURS", "OUT", "SEE", "SHE", "TOO", "TWO", "USE",
    "WAS", "WAY", "YOU", "YOUR", "YOURS", "ME", "MY", "WE", "US",
    "THEY", "THEM", "THEIR", "THEIRS", "MINE",
    # Common verbs / adjectives
    "ABOUT", "AFTER", "AGAIN", "BELOW", "COULD", "EVERY", "FIRST",
    "FOUND", "GREAT", "HOUSE", "LARGE", "LEARN", "NEVER", "OTHER",
    "PLACE", "PLANT", "POINT", "RIGHT", "SMALL", "SOUND", "SPELL",
    "STILL", "STUDY", "THERE", "THESE", "THING", "THINK", "THREE",
    "WATER", "WORLD", "WOULD", "WRITE",
    # Financial vocabulary
    "CHECK", "PRICE", "PRICES", "STOCK", "STOCKS", "SHARE", "SHARES",
    "TOTAL", "VALUE", "MONEY", "TRADE", "TODAY", "CURRENT", "COMPARE",
    "VERSUS", "VOLUME", "HIGH", "LOW", "CLOSE", "OPEN", "RATE", "RATES",
    "COST", "COSTS", "COIN", "COINS",
    # Assistant vocabulary
    "WEATHER", "NEWS", "TIME", "DATE", "SEARCH", "LOOK", "TELL",
    "PLEASE", "THANK", "THANKS", "HELLO", "HEY", "HI", "GOOD", "LIKE",
    "WANT", "SHOW", "FIND", "NEW", "SOME", "SUCH", "THAN", "VERY",
    "JUST", "ALSO", "WILL", "SHALL", "SHOULD", "MAY", "MIGHT", "MUST",
    "GIVE", "VIEW", "YES", "TRUE", "FALSE",
    # Places (frequently mis-tagged as tickers)
    "YORK", "CITY", "TOWN", "LONDON", "TOKYO", "PARIS", "BERLIN",
    "DELHI", "MUMBAI", "OHIO", "OREGON", "TEXAS", "FLORIDA", "CALIFORNIA",
    "AFRICA", "AMERICA", "EUROPE", "ASIA", "INDIA", "CHINA", "JAPAN",
    "CAPE", "SOUTH", "NORTH", "EAST", "WEST", "PORTLAND", "COLUMBUS",
    "FRANCISCO", "SAN", "ANGELES", "LOS", "NEW",
    # Technology / object vocabulary
    "MOBILE", "PHONE", "CARD", "CARDS", "ELECTRONIC", "ELECTRONICS",
    "LINE", "LINES", "DEAD", "LIVE", "ALIVE", "WORKS", "WORK",
    "HELP", "HELPS", "HELPED", "NEED", "NEEDS", "WANTED", "TRYING",
    "SORRY", "OKAY", "SURE", "YEAH", "NOPE", "HMM", "WELL",
}


def _extract_symbols(text: str) -> List[str]:
    """
    Extract ALL ticker symbols from text, strictly preserving appearance order.

    Handles:
      - Company names (word bounded): "apple" -> "AAPL"
      - Contextual / prefixed tickers: "check intc", "$AMD", "price of amd" -> "INTC", "AMD"
      - Known ticker direct match (case-insensitive): "what about nvda" -> "NVDA"
      - Raw uppercase tickers: "AAPL" -> "AAPL"  (only if in KNOWN_TICKERS)
      - Multiple: "compare apple and tesla" -> ["AAPL", "TSLA"]

    Safety rule: a bare uppercase word is only treated as a ticker if it is in
    KNOWN_TICKERS. This prevents "THIS", "OHIO", "DEAD", "LNC" (from "line's")
    and similar false positives from ever reaching the tool layer.
    """
    text_clean = text.strip()
    if not text_clean:
        return []

    candidates: List[Tuple[int, int, str]] = []

    # 1. Company names (word-bounded, matched by longest name first)
    for name in sorted(COMPANY_TO_TICKER.keys(), key=len, reverse=True):
        pattern = r"\b" + re.escape(name) + r"\b"
        for m in re.finditer(pattern, text_clean, re.IGNORECASE):
            ticker = COMPANY_TO_TICKER[name]
            candidates.append((m.start(), m.end(), ticker))

    # 2. Prefixed ticker patterns (e.g. $AAPL, check INTC, price of amd, stock tsla)
    for m in PREFIXED_TICKER_PATTERN.finditer(text_clean):
        word = m.group(1)
        word_upper = word.upper()
        word_lower = word.lower()
        if word_lower in COMPANY_TO_TICKER:
            candidates.append((m.start(1), m.end(1), COMPANY_TO_TICKER[word_lower]))
        elif word_upper in KNOWN_TICKERS:
            candidates.append((m.start(1), m.end(1), word_upper))
        # else: ignore - don't guess at unknown uppercase words

    # 3. Known tickers directly mentioned in text (case-insensitive)
    for m in re.finditer(r"\b[A-Za-z]{1,5}\b", text_clean):
        word_upper = m.group(0).upper()
        if word_upper in KNOWN_TICKERS and word_upper not in COMMON_WORDS_NOT_TICKERS:
            candidates.append((m.start(), m.end(), word_upper))

    # 4. Raw uppercase tickers — ONLY accept if in KNOWN_TICKERS.
    # This is the critical safety gate that eliminates THIS/OHIO/DEAD/etc.
    for m in TICKER_PATTERN.finditer(text_clean):
        ticker = m.group(0)
        if ticker in KNOWN_TICKERS and ticker not in COMMON_WORDS_NOT_TICKERS:
            candidates.append((m.start(), m.end(), ticker))

    # Sort candidates by start position ascending, then by match length descending
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))

    chosen_spans: List[Tuple[int, int]] = []
    symbols: List[str] = []
    seen: Set[str] = set()

    for start, end, ticker in candidates:
        # Avoid overlapping tokens
        if any(not (end <= s or start >= e) for s, e in chosen_spans):
            continue
        chosen_spans.append((start, end))
        if ticker not in seen:
            symbols.append(ticker)
            seen.add(ticker)

    return symbols


def _extract_field(text: str) -> str:
    """Extract price vs volume intent."""
    text_lower = text.lower()
    if "volume" in text_lower:
        return "volume"
    return "price"


def _is_comparison_query(text: str) -> bool:
    """Detect if the user is asking for a comparison."""
    text_lower = text.lower()
    for conj in COMPARISON_CONJUNCTIONS:
        if conj in text_lower:
            return True
    if text_lower.startswith("compare") or text_lower.startswith("vs"):
        return True
    return False


def _is_likely_continuation(text: str) -> bool:
    """
    Return True only if this utterance plausibly continues the previous intent
    without introducing a new subject.

    Used to decide whether to inherit symbols/field from current_intent.
    """
    text_lower = text.lower().strip()
    if not text_lower:
        return False

    # Any correction marker -> NOT a continuation
    if any(p.search(text_lower) for p in _CORRECTION_PATTERNS):
        return False

    # Very short utterances ("and?", "what about that") -> continuation
    words = text_lower.split()
    if len(words) <= 3:
        return True

    # Otherwise, assume new intent unless explicitly a follow-up phrase
    followups = ["and what about", "what about", "how about", "and"]
    return any(text_lower.startswith(f) for f in followups)


def _detect_intent_change(
    text: str,
    current_intent: Dict[str, Any],
    symbols: List[str],
    field: str,
) -> Tuple[bool, str]:
    """
    Determine if the new utterance represents a true intent change.
    Returns (is_new_request, reason)
    """
    text_lower = text.lower()

    if not current_intent:
        return True, "initial_request"

    if any(pattern.search(text_lower) for pattern in _CORRECTION_PATTERNS):
        return True, "correction_marker"

    current_symbols = current_intent.get("symbols", [])
    current_field = current_intent.get("field", "price")
    field_changed = field != current_field

    if not symbols:
        if field_changed:
            return True, "field_change_no_symbol"
        # No symbols detected — only inherit if this looks like a continuation
        if _is_likely_continuation(text):
            return False, "likely_continuation"
        return True, "no_symbol_in_utterance"

    if set(symbols) != set(current_symbols):
        return True, "symbol_change"

    if field_changed:
        return True, "field_change"

    return False, "same_intent"


def route_intent(text: str, current_intent: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """
    Route user utterance to an intent.

    Returns:
      - intent: dict with 'symbols', 'field', 'is_comparison', 'raw_text', 'tool'
      - is_new_request: True if this should cancel an in-flight tool call
    """
    text_clean = text.strip()
    if not text_clean:
        return {}, False

    symbols = _extract_symbols(text_clean)
    field = _extract_field(text_clean)
    is_comparison = _is_comparison_query(text_clean)

    is_new, reason = _detect_intent_change(text_clean, current_intent, symbols, field)

    # Only inherit previous symbols when this is explicitly a continuation
    # (short follow-up without a new subject) AND we found no new symbols.
    if symbols:
        effective_symbols = symbols
    elif not is_new and _is_likely_continuation(text_clean):
        effective_symbols = current_intent.get("symbols", [])
    else:
        effective_symbols = []

    effective_is_comparison = is_comparison and len(effective_symbols) > 1

    intent = {
        "raw_text": text_clean,
        "symbols": effective_symbols,
        "field": field,
        "is_comparison": effective_is_comparison,
        "tool": "compare_stocks" if effective_is_comparison else "stock_quote",
        "_reason": reason,  # internal, for logging
    }

    return intent, is_new
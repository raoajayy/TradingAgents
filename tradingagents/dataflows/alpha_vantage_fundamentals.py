import json
from datetime import date, datetime

from .alpha_vantage_common import _make_api_request


def _is_past(curr_date: str | None) -> bool:
    """True when curr_date is a real past date (i.e. a backtest as-of)."""
    if not curr_date:
        return False
    try:
        return datetime.strptime(curr_date, "%Y-%m-%d").date() < date.today()
    except ValueError:
        return False


def _filter_reports_by_date(result, curr_date: str):
    """Drop annual/quarterly reports dated after curr_date to prevent look-ahead.

    ``_make_api_request`` returns the fundamentals payload as a JSON string, so
    parse, filter, and re-serialize. A non-JSON body or an unset ``curr_date`` is
    returned unchanged.
    """
    if not curr_date or not isinstance(result, str):
        return result
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return result
    if not isinstance(payload, dict):
        return result
    for key in ("annualReports", "quarterlyReports"):
        if isinstance(payload.get(key), list):
            payload[key] = [
                r for r in payload[key]
                if r.get("fiscalDateEnding", "") <= curr_date
            ]
    return json.dumps(payload)


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd

    Returns:
        str: Company overview data including financial ratios and key metrics
    """
    # CI-7: Alpha Vantage OVERVIEW is a CURRENT-ONLY snapshot (market cap, P/E,
    # latest-quarter figures) with no point-in-time history. Returning it for a
    # PAST curr_date leaks present-day fundamentals into a backtest. Disclose
    # and withhold rather than silently leak.
    if _is_past(curr_date):
        return json.dumps({
            "symbol": ticker,
            "as_of_requested": curr_date,
            "point_in_time": False,
            "note": "Alpha Vantage OVERVIEW is a current-only snapshot with no "
                    "historical as-of; withheld to avoid look-ahead leakage in "
                    "a backtest. Use the dated statement endpoints instead.",
        })

    params = {
        "symbol": ticker,
    }

    return _make_api_request("OVERVIEW", params)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve balance sheet data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("BALANCE_SHEET", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve cash flow statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("CASH_FLOW", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve income statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("INCOME_STATEMENT", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date)


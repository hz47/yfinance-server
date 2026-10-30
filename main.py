import yfinance as yf
from typing import Annotated
from enum import Enum
from pydantic import BaseModel
from fastapi import Depends, FastAPI, Header, HTTPException
from typing import Union, List, Optional
import numpy as np
import pandas as pd
import datetime
import math
import os

app = FastAPI()

apiKey1 = os.getenv('API_KEY1')
apiKey2 = os.getenv('API_KEY2')
apiKeyPresent = apiKey1 is not None or apiKey2 is not None

# attributes that open long-lived connections or expose internals, never callable over the API
BLOCKED_METHODS = {'live', 'session'}


class InvokeRequest(BaseModel):
    symbol: str
    method: str
    # Optional params list of ints or strings, defaults to None
    params: dict = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "symbol": "MSFT",
                    "method": "info"
                },
                {
                    "symbol": "AAPL",
                    "method": "history",
                    "params": {
                        "period": "1mo",
                    }
                }
            ]
        }
    }


class ObjectKind(str, Enum):
    sector = "sector"
    industry = "industry"
    market = "market"
    search = "search"
    lookup = "lookup"


# path kind -> yfinance class, constructed as cls(key, **init)
OBJECT_CLASSES = {
    ObjectKind.sector: yf.Sector,
    ObjectKind.industry: yf.Industry,
    ObjectKind.market: yf.Market,
    ObjectKind.search: yf.Search,
    ObjectKind.lookup: yf.Lookup,
}


class ObjectInvokeRequest(BaseModel):
    # sector/industry key, market name ("US", "EUROPE", ...) or search/lookup query
    key: str
    method: str
    # keyword arguments for the method, if it is callable
    params: Optional[dict] = None
    # keyword arguments for the constructor, e.g. {"region": "US"} or {"max_results": 20}
    init: Optional[dict] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"key": "technology", "method": "top_companies"},
                {"key": "US", "method": "summary"},
                {"key": "nvidia", "method": "quotes", "init": {"max_results": 5}},
                {"key": "apple", "method": "get_stock", "params": {"count": 10}},
            ]
        }
    }


class DownloadRequest(BaseModel):
    tickers: Union[str, List[str]]
    # keyword arguments for yf.download, e.g. {"period": "5d", "interval": "1d"}
    params: Optional[dict] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"tickers": ["AAPL", "MSFT"], "params": {"period": "5d"}}
            ]
        }
    }


class ScreenRequest(BaseModel):
    # predefined screener name, see yf.PREDEFINED_SCREENER_QUERIES
    query: str
    # keyword arguments for yf.screen, e.g. {"count": 25}
    params: Optional[dict] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"query": "day_gainers", "params": {"count": 10}}
            ]
        }
    }


def check_api_key(x_api_key: Annotated[str | None, Header()] = None):
    if apiKeyPresent:
        if x_api_key is None:
            raise HTTPException(
                status_code=401, detail="API key is missing"
            )
        if x_api_key != apiKey1 and x_api_key != apiKey2:
            raise HTTPException(
                status_code=403, detail="API key is invalid"
            )


def to_jsonable(value):
    # pandas/numpy results -> plain JSON; NaN/inf become null instead of failing serialization
    if isinstance(value, pd.DataFrame):
        if isinstance(value.columns, pd.MultiIndex):
            return {str(k): to_jsonable(value[k]) for k in value.columns.get_level_values(0).unique()}
        return to_jsonable(value.to_dict())
    if isinstance(value, pd.Series):
        return to_jsonable(value.to_dict())
    if isinstance(value, tuple) and hasattr(value, '_asdict'):
        return to_jsonable(value._asdict())
    if isinstance(value, dict):
        return {(str(k) if isinstance(k, tuple) else to_jsonable(k)): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is pd.NaT:
        return None
    if hasattr(value, 'keys') and hasattr(value, '__getitem__'):
        # dict-like objects such as Ticker.fast_info
        result = {}
        for k in value.keys():
            try:
                result[k] = to_jsonable(value[k])
            except Exception:
                result[k] = None
        return result
    if value is None or isinstance(value, (str, int, float, datetime.date, datetime.time, datetime.timedelta)):
        return value
    # anything else (timezones, custom yfinance objects) is returned as its string form
    return str(value)


def invoke(target, method_name: str, params: Optional[dict]):
    if method_name.startswith('_') or method_name in BLOCKED_METHODS:
        raise HTTPException(
            status_code=404, detail="Method not found"
        )
    method = getattr(target, method_name, None)
    if method is None:
        raise HTTPException(
            status_code=404, detail="Method not found"
        )
    if callable(method):
        try:
            result = method(**(params or {}))
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        # properties such as info, financials or dividends
        result = method
    return to_jsonable(result)


@app.get("/api/info")
def read_root():
    # read version from file
    version = None
    with open('last-release-version.txt') as f:
        version = f.read().strip()

    yfinanceVersion = yf.__version__
    return {"hello": "world", "version": version if version else "unknown", "now": pd.Timestamp.now(), "yfinanceVersion": yfinanceVersion}


@app.post("/api/invoke/ticker", dependencies=[Depends(check_api_key)])
def read_item(request: InvokeRequest):
    ticker = yf.Ticker(request.symbol)
    return invoke(ticker, request.method, request.params)


@app.post("/api/invoke/{kind}", dependencies=[Depends(check_api_key)])
def invoke_object(kind: ObjectKind, request: ObjectInvokeRequest):
    try:
        target = OBJECT_CLASSES[kind](request.key, **(request.init or {}))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return invoke(target, request.method, request.params)


@app.post("/api/download", dependencies=[Depends(check_api_key)])
def download(request: DownloadRequest):
    params = {**(request.params or {}), "progress": False}
    try:
        result = yf.download(request.tickers, **params)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return to_jsonable(result)


@app.get("/api/screen", dependencies=[Depends(check_api_key)])
def list_screens():
    return sorted(yf.PREDEFINED_SCREENER_QUERIES.keys())


@app.post("/api/screen", dependencies=[Depends(check_api_key)])
def screen(request: ScreenRequest):
    if request.query not in yf.PREDEFINED_SCREENER_QUERIES:
        raise HTTPException(
            status_code=404, detail="Screener not found"
        )
    try:
        result = yf.screen(request.query, **(request.params or {}))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return to_jsonable(result)

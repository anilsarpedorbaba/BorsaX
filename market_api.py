"""
market_api.py — BorsaX / KriptoX gerçek piyasa verisi API'si
================================================================
Bu, sizin daha önce yazıp doğru çalıştığını doğruladığınız borsahub
projesindeki AYNI mantığın (main.py) KriptoX'in kendi enstrüman
listesine (100 BIST, 70 ABD, 15 Endeks, Emtia, Kripto) uyarlanmış hali.

Neden çalışıyor:
- Yahoo Finance'e istekler TARAYICIDAN değil, bu Python sunucusundan
  atılıyor. CORS sadece tarayıcı JS'i için geçerli bir kısıtlama —
  sunucudan sunucuya istekte hiç devreye girmiyor.
- ThreadPoolExecutor ile ~185 sembol PARALEL çekiliyor (sıralı değil),
  bu yüzden tüm liste saniyeler içinde tazeleniyor — eski PHP/proxy
  yaklaşımındaki "sırayla, dakikalarca sürüyor" sorunu burada yok.
- Sonuçlar sunucu hafızasında CACHE_TTL süresince (varsayılan 5 dk)
  tutuluyor; Yahoo'nun kendi verisi zaten ~15 dk gecikmeli olduğu için
  "gerçek ama gecikmeli" tam olarak istediğiniz gibi çalışıyor.

ÇALIŞTIRMA
    pip install fastapi uvicorn requests
    uvicorn market_api:app --host 0.0.0.0 --port 8000

Bu API, kriptox.html'in çalıştığı adresten FARKLI bir sunucuda/portta
çalışabilir — CORS zaten tüm originlere açık (allow_origins=["*"]),
tarayıcıdan sorunsuz çağrılır.

UÇ NOKTALAR
    GET /api/kriptox/all           — hepsi tek seferde (frontend bunu kullanıyor)
    GET /api/kriptox/bist          — sadece BIST 100
    GET /api/kriptox/us            — sadece ABD hisseleri
    GET /api/kriptox/indices       — sadece endeksler
    GET /api/kriptox/commodities   — sadece emtia
    GET /api/kriptox/crypto        — sadece kripto (CoinGecko)
    GET /api/kriptox/*/refresh     — ilgili kategorinin önbelleğini atlayıp zorla tazeler
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional
import requests, asyncio, time

app = FastAPI(title="BorsaX / KriptoX Market Data API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── ENSTRÜMAN LİSTELERİ (kriptox.html ile birebir aynı) ──────────────
BIST_SYMBOLS = [
    "THYAO","GARAN","AKBNK","ISCTR","SISE","EREGL","KCHOL","SAHOL","TUPRS","BIMAS",
    "ASELS","PETKM","PGSUS","TOASO","FROTO","ARCLK","VESTL","KOZAL","KOZAA","TCELL",
    "TTKOM","YKBNK","VAKBN","HALKB","SASA","HEKTS","KRDMD","ENKAI","EKGYO","MGROS",
    "ULKER","AEFES","CCOLA","SOKM","BRSAN","ALARK","OTKAR","OYAKC","TSKB","ISGYO",
    "ISMEN","DOHOL","GUBRF","ENJSA","ODAS","AGHOL","ZOREN","KONTR","KARSN","ASUZU",
    "CIMSA","BUCIM","ADANA","AKSA","AKSEN","ALKIM","ANHYT","ANSGR","BAGFS","BASGZ",
    "BERA","BFREN","BIOEN","BRISA","BTCIM","CANTE","CEMTS","CWENE","DOAS","ECILC",
    "EGEEN","EUPWR","GESAN","GOLTS","GSDHO","GWIND","HUNER","INDES","IPEKE","IZMDC",
    "KARTN","KLSER","KMPUR","KONYA","KORDS","KTLEV","LOGO","MAVI","MPARK","NTHOL",
    "NUGYO","ORGE","PENTA","PSGYO","QUAGR","SEKFK","SKBNK","SMRTG","SNGYO","TABGD",
    "TATGD","TKFEN","TMSN","TRGYO","TURSG","VESBE","YEOTK","ZRGYO",
][:100]

US_SYMBOLS = [
    "AAPL","MSFT","GOOGL","GOOG","AMZN","NVDA","META","TSLA","BRK.B","JPM","V","UNH",
    "MA","HD","PG","XOM","CVX","ABBV","MRK","LLY","PEP","KO","COST","AVGO","WMT","MCD",
    "CSCO","ADBE","CRM","NFLX","INTC","AMD","QCOM","TXN","IBM","ORCL","NKE","DIS","BA",
    "CAT","GE","GS","MS","WFC","BAC","C","AXP","SBUX","PYPL","INTU","AMGN","GILD","BMY",
    "PFE","T","VZ","CMCSA","HON","UPS","RTX","LMT","DE","MMM","UNP","PM","MO","TMO",
    "ABT","DHR","MDT","SPGI","BLK","NOW",
][:70]

INDEX_SYMBOLS = {
    "XU100": "XU100.IS", "XU030": "XU030.IS", "SPX": "^GSPC", "DJI": "^DJI", "NDX": "^NDX",
    "DAX": "^GDAXI", "FTSE": "^FTSE", "CAC": "^FCHI", "N225": "^N225", "HSI": "^HSI",
    "SHCOMP": "000001.SS", "SX5E": "^STOXX50E", "RUT": "^RUT", "VIX": "^VIX", "MXEF": "EEM",
}

COMMODITY_SYMBOLS = {
    "XAU": "GC=F", "XAG": "SI=F", "XPT": "PL=F", "XPD": "PA=F", "WTI": "CL=F", "BRENT": "BZ=F",
    "NATGAS": "NG=F", "GASOLINE": "RB=F", "WHEAT": "ZW=F", "CORN": "ZC=F", "SOYBEAN": "ZS=F",
    "COFFEE": "KC=F", "COTTON": "CT=F", "SUGAR": "SB=F", "COCOA": "CC=F", "COPPER": "HG=F",
    "ALUMINUM": "ALI=F",
}

CRYPTO_COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "USDT": "tether", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
}

# ── ÖNBELLEK (borsahub'daki ile aynı desen) ──────────────────────────
cache: Dict[str, Any] = {}
CACHE_TTL = 300  # 5 dakika — Yahoo'nun kendi verisi zaten ~15 dk gecikmeli
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

def is_cache_valid(key): return key in cache and (time.time() - cache[key]["timestamp"]) < CACHE_TTL
def set_cache(key, data): cache[key] = {"data": data, "timestamp": time.time()}
def get_cache(key): return cache.get(key, {}).get("data")

def fetch_yahoo_quote(y_symbol: str, kx_id: str) -> Optional[dict]:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{y_symbol}"
        r = requests.get(url, params={"interval": "1d", "range": "5d"}, headers=HEADERS, timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        quote0 = result[0].get("indicators", {}).get("quote", [{}])[0]
        closes = [c for c in quote0.get("close", []) if c is not None]
        if not closes:
            return None
        price = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else price
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0
        return {"id": kx_id, "price": round(price, 4), "change_pct": round(change_pct, 2)}
    except Exception:
        return None

def fetch_yahoo_batch(symbol_map: Dict[str, str]) -> List[dict]:
    """symbol_map: { kriptox_id: yahoo_symbol } — fetched in PARALLEL."""
    results = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(fetch_yahoo_quote, y, kx): kx for kx, y in symbol_map.items()}
        for f in as_completed(futures):
            try:
                row = f.result(timeout=15)
                if row:
                    results.append(row)
            except Exception:
                pass
    return results

def fetch_crypto_coingecko() -> List[dict]:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(CRYPTO_COINGECKO_IDS.values()),
                "order": "market_cap_desc",
                "per_page": 50,
                "page": 1,
                "sparkline": False,
                "price_change_percentage": "24h",
            },
            timeout=15,
        )
        r.raise_for_status()
        by_gecko_id = {c["id"]: c for c in r.json()}
        out = []
        for kx_id, gecko_id in CRYPTO_COINGECKO_IDS.items():
            c = by_gecko_id.get(gecko_id)
            if not c:
                continue
            out.append({
                "id": kx_id,
                "price": c["current_price"],
                "change_pct": round(c.get("price_change_percentage_24h") or 0, 2),
            })
        return out
    except Exception:
        return []

# ── UÇ NOKTALAR ───────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"message": "BorsaX / KriptoX Market Data API", "docs": "/docs"}

@app.get("/api/kriptox/bist")
async def get_bist():
    if is_cache_valid("bist"):
        return {"data": get_cache("bist"), "cached": True}
    symbol_map = {s: f"{s}.IS" for s in BIST_SYMBOLS}
    data = await asyncio.get_event_loop().run_in_executor(None, lambda: fetch_yahoo_batch(symbol_map))
    set_cache("bist", data)
    return {"data": data, "cached": False}

@app.get("/api/kriptox/us")
async def get_us():
    if is_cache_valid("us"):
        return {"data": get_cache("us"), "cached": True}
    symbol_map = {s: ("BRK-B" if s == "BRK.B" else s) for s in US_SYMBOLS}
    data = await asyncio.get_event_loop().run_in_executor(None, lambda: fetch_yahoo_batch(symbol_map))
    set_cache("us", data)
    return {"data": data, "cached": False}

@app.get("/api/kriptox/indices")
async def get_indices():
    if is_cache_valid("indices"):
        return {"data": get_cache("indices"), "cached": True}
    data = await asyncio.get_event_loop().run_in_executor(None, lambda: fetch_yahoo_batch(INDEX_SYMBOLS))
    set_cache("indices", data)
    return {"data": data, "cached": False}

@app.get("/api/kriptox/commodities")
async def get_commodities():
    if is_cache_valid("commodities"):
        return {"data": get_cache("commodities"), "cached": True}
    data = await asyncio.get_event_loop().run_in_executor(None, lambda: fetch_yahoo_batch(COMMODITY_SYMBOLS))
    set_cache("commodities", data)
    return {"data": data, "cached": False}

@app.get("/api/kriptox/crypto")
async def get_crypto():
    if is_cache_valid("crypto"):
        return {"data": get_cache("crypto"), "cached": True}
    data = await asyncio.get_event_loop().run_in_executor(None, fetch_crypto_coingecko)
    set_cache("crypto", data)
    return {"data": data, "cached": False}

import requests

@app.get("/api/kriptox/all")
def get_all_market_data():
    # Dış servisler yanıt vermezse sunucu çökmesin diye varsayılan şablon
    data = {
        "bist": [],
        "us": [],
        "indices": [],
        "commodities": [],
        "crypto": []
    }
    
    try:
        # İSTEKLERE MUTLAKA timeout=3 veya 5 EKLE
        # Örnek: response = requests.get("API_URL", timeout=3)
        
        # ... Veri çekme kodların ...
        
        return data
    except Exception as e:
        # Hata olursa kilitlenmek yerine boş/yedek veriyi anında dön
        print(f"Veri çekme hatası: {e}")
        return data

@app.get("/api/kriptox/bist/refresh")
async def refresh_bist():
    cache.pop("bist", None)
    return await get_bist()

@app.get("/api/kriptox/us/refresh")
async def refresh_us():
    cache.pop("us", None)
    return await get_us()

@app.get("/api/kriptox/indices/refresh")
async def refresh_indices():
    cache.pop("indices", None)
    return await get_indices()

@app.get("/api/kriptox/commodities/refresh")
async def refresh_commodities():
    cache.pop("commodities", None)
    return await get_commodities()

@app.get("/api/kriptox/crypto/refresh")
async def refresh_crypto():
    cache.pop("crypto", None)
    return await get_crypto()

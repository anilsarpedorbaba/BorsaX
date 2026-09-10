import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Son çekilen gerçek verileri hafızada tutacak yapı
CACHE = {
    "data": None,
    "last_updated": 0
}
CACHE_TTL = 300  # 5 dakika (300 saniye)

def fetch_real_prices():
    """Yahoo Finance üzerinden gerçek canlı fiyatları çeker."""
    tickers = {
        "bist": ["THYAO.IS", "GARAN.IS", "ASELS.IS"],
        "us": ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN"],
        "indices": ["^XU100", "^IXIC", "^GSPC"],
        "commodities": ["GC=F", "CL=F"],
        "crypto": ["BTC-USD", "ETH-USD", "SOL-USD"]
    }
    
    result = {"bist": [], "us": [], "indices": [], "commodities": [], "crypto": []}

    for cat, syms in tickers.items():
        for sym in syms:
            try:
                t = yf.Ticker(sym)
                # fast_info veya history ile en güncel son kapanış/canlı fiyatı alıyoruz
                price = t.fast_info.last_price or t.fast_info.previous_close
                if price:
                    result[cat].append({"sym": sym, "price": round(float(price), 2)})
            except Exception:
                continue

    return result

@app.get("/api/kriptox/all")
def get_all_market_data():
    now = time.time()
    
    # Eğer önbellekte gerçek veri varsa ve 5 dakikadan eskiyse VEYA önbellek henüz boşsa yenile
    if not CACHE["data"] or (now - CACHE["last_updated"] > CACHE_TTL):
        try:
            fresh_data = fetch_real_prices()
            # Eğer en azından bazı veriler çekildiyse önbelleği güncelle
            if any(len(v) > 0 for v in fresh_data.values()):
                CACHE["data"] = fresh_data
                CACHE["last_updated"] = now
        except Exception as e:
            print(f"Veri güncelleme hatası: {e}")

    # Eğer canlı veri çekildiyse önbellekten dön, hiç çekilemediyse eski önbelleği dön
    if CACHE["data"]:
        return CACHE["data"]

    return {"bist": [], "us": [], "indices": [], "commodities": [], "crypto": []}

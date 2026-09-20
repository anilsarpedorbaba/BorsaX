"""
BorsaX Tam Backend — Kullanıcı kaydı + SQLite + Piyasa verisi
Çalıştırma: uvicorn app:app --host 0.0.0.0 --port $PORT
"""
import sqlite3, json, os, time, secrets, hashlib, asyncio
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

DB_PATH = os.environ.get("DB_PATH", "borsax.db")
SESSION_TTL = 60 * 60 * 24 * 30

app = FastAPI(title="BorsaX API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${h}"

def verify_password(pw: str, stored: str) -> bool:
    if not stored: return False
    if "$" not in stored: return secrets.compare_digest(pw, stored)
    salt, h = stored.split("$", 1)
    return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex(), h)

def db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY, password TEXT NOT NULL, data TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS admins (
        username TEXT PRIMARY KEY, password TEXT NOT NULL, display_name TEXT NOT NULL,
        is_super INTEGER NOT NULL DEFAULT 0, permissions TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL,
        created_at REAL NOT NULL, expires_at REAL NOT NULL);
    """)
    c.commit()
    if not c.execute("SELECT 1 FROM admins WHERE username='admin'").fetchone():
        perms = json.dumps({"balance": True, "positions": True, "deposits": True,
                            "withdrawals": True, "kyc": True, "manageAdmins": True})
        c.execute("INSERT INTO admins VALUES (?,?,?,?,?,?)",
                  ("admin", hash_password("admin123"), "Baş Yönetici", 1, perms, time.time()))
        c.commit()
    if not c.execute("SELECT 1 FROM users WHERE username='demo'").fetchone():
        now_str = time.strftime("%d.%m.%Y %H:%M")
        demo = {
            "fullName": "Demo Kullanıcı", "phone": "+90 555 010 20 30",
            "email": "demo@example.com", "tc": "12345678901",
            "registeredAt": now_str, "balance": 2100890.0, "futuresBalance": 467000.0,
            "positions": [
                {"id":"seed1","symDisplay":"BTC/USDT","color":"#f0a83c","side":"Long","qty":0.25,
                 "qtyLabel":"0.250 BTC","entry":64250.0,"cat":"crypto","tickerSym":"BTC","currency":"USDT"},
                {"id":"seed2","symDisplay":"ETH/USDT","color":"#4d8cf6","side":"Short","qty":5.0,
                 "qtyLabel":"5.000 ETH","entry":2890.5,"cat":"crypto","tickerSym":"ETH","currency":"USDT"},
            ],
            "closedPositions": [], "recentTx": [],
            "depositHistory": [], "withdrawHistory": [], "transferHistory": [],
            "kycStatus": "Onaylandı", "kycDocuments": [],
            "balanceAdjustments": [], "notifications": [],
            "settings": {"inapp": True, "email": True, "sms": False, "market": True, "currency": "TRY"},
        }
        c.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                  ("demo", hash_password("demo123"), json.dumps(demo, ensure_ascii=False), time.time(), time.time()))
        c.commit()
    c.close()

init_db()

def create_session(username: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    c = db()
    c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
              (token, username, role, time.time(), time.time() + SESSION_TTL))
    c.commit(); c.close()
    return token

def get_session(token: Optional[str]):
    if not token: return None
    c = db()
    row = c.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    c.close()
    if not row or row["expires_at"] < time.time(): return None
    return {"username": row["username"], "role": row["role"]}

def require_user(authorization: Optional[str] = Header(None)):
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    s = get_session(token)
    if not s: raise HTTPException(401, "Oturum gerekli")
    return s

def require_admin(authorization: Optional[str] = Header(None)):
    s = require_user(authorization)
    if s["role"] != "admin": raise HTTPException(403, "Yönetici yetkisi gerekli")
    return s

def admin_dict(row):
    return {"username": row["username"], "displayName": row["display_name"],
            "isSuper": bool(row["is_super"]), "permissions": json.loads(row["permissions"])}

class RegisterBody(BaseModel):
    username: str
    password: str
    fullName: str = ""
    phone: str = ""

class LoginBody(BaseModel):
    username: str
    password: str

@app.post("/api/auth/register")
def register(body: RegisterBody):
    uname = body.username.strip().lower()
    if len(uname) < 3 or len(body.password) < 4:
        raise HTTPException(400, "Kullanıcı adı en az 3, şifre en az 4 karakter olmalı")
    c = db()
    if c.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone():
        c.close(); raise HTTPException(400, "Bu kullanıcı adı zaten kayıtlı")
    if c.execute("SELECT 1 FROM admins WHERE username=?", (uname,)).fetchone():
        c.close(); raise HTTPException(400, "Bu kullanıcı adı zaten kayıtlı")
    seed = sum((i + 7) * ord(ch) for i, ch in enumerate(body.username))
    tc_digits = str(10000000000 + (seed * 9973) % 89999999999).zfill(11)
    now_str = time.strftime("%d.%m.%Y %H:%M")
    data = {
        "fullName": body.fullName or body.username, "phone": body.phone or "—",
        "email": uname + "@example.com", "tc": tc_digits, "registeredAt": now_str,
        "balance": 0, "futuresBalance": 0,
        "positions": [], "closedPositions": [], "recentTx": [],
        "depositHistory": [], "withdrawHistory": [], "transferHistory": [],
        "kycStatus": "Evrak Yok", "kycDocuments": [], "balanceAdjustments": [],
        "notifications": [{"id": "n" + str(int(time.time())), "type": "ok",
                           "title": "BorsaX'e hoş geldiniz!",
                           "desc": "Hesabınız başarıyla oluşturuldu.",
                           "date": now_str, "read": False}],
        "settings": {"inapp": True, "email": True, "sms": False, "market": True, "currency": "TRY"},
    }
    now = time.time()
    c.execute("INSERT INTO users VALUES (?,?,?,?,?)",
              (uname, hash_password(body.password), json.dumps(data, ensure_ascii=False), now, now))
    c.commit(); c.close()
    token = create_session(uname, "user")
    return {"token": token, "role": "user", "username": uname, "data": data}

@app.post("/api/auth/login")
def login(body: LoginBody):
    uname = body.username.strip().lower()
    c = db()
    a = c.execute("SELECT * FROM admins WHERE username=?", (uname,)).fetchone()
    if a and verify_password(body.password, a["password"]):
        c.close()
        token = create_session(uname, "admin")
        return {"token": token, "role": "admin", "username": uname, "admin": admin_dict(a)}
    u = c.execute("SELECT * FROM users WHERE username=?", (uname,)).fetchone()
    c.close()
    if not u or not verify_password(body.password, u["password"]):
        raise HTTPException(401, "Kullanıcı adı veya şifre hatalı")
    token = create_session(uname, "user")
    return {"token": token, "role": "user", "username": uname, "data": json.loads(u["data"])}

@app.get("/api/me")
def me(s=Depends(require_user)):
    c = db()
    if s["role"] == "admin":
        a = c.execute("SELECT * FROM admins WHERE username=?", (s["username"],)).fetchone()
        c.close()
        return {"role": "admin", "username": s["username"], "admin": admin_dict(a)}
    u = c.execute("SELECT * FROM users WHERE username=?", (s["username"],)).fetchone()
    c.close()
    if not u: raise HTTPException(404, "Kullanıcı bulunamadı")
    return {"role": "user", "username": s["username"], "data": json.loads(u["data"])}

@app.put("/api/me/data")
def save_my_data(payload: Dict[str, Any], s=Depends(require_user)):
    if s["role"] != "user": raise HTTPException(403, "Sadece kullanıcı")
    c = db()
    c.execute("UPDATE users SET data=?, updated_at=? WHERE username=?",
              (json.dumps(payload, ensure_ascii=False), time.time(), s["username"]))
    c.commit(); c.close()
    return {"ok": True}

@app.get("/api/admin/users")
def admin_users(s=Depends(require_admin)):
    c = db()
    rows = c.execute("SELECT username, data FROM users ORDER BY created_at DESC").fetchall()
    c.close()
    return {"users": {r["username"]: json.loads(r["data"]) for r in rows}}

@app.put("/api/admin/users/{uname}/data")
def admin_save_user(uname: str, payload: Dict[str, Any], s=Depends(require_admin)):
    c = db()
    cur = c.execute("UPDATE users SET data=?, updated_at=? WHERE username=?",
                    (json.dumps(payload, ensure_ascii=False), time.time(), uname))
    c.commit()
    ok = cur.rowcount > 0
    c.close()
    if not ok: raise HTTPException(404, "Kullanıcı bulunamadı")
    return {"ok": True}

@app.get("/api/admin/admins")
def admin_list(s=Depends(require_admin)):
    c = db()
    rows = c.execute("SELECT * FROM admins").fetchall()
    c.close()
    return {"admins": {r["username"]: admin_dict(r) for r in rows}}

class NewAdminBody(BaseModel):
    username: str
    password: str
    displayName: str
    permissions: Dict[str, bool] = {}

@app.post("/api/admin/admins")
def admin_create(body: NewAdminBody, s=Depends(require_admin)):
    c = db()
    me_row = c.execute("SELECT permissions FROM admins WHERE username=?", (s["username"],)).fetchone()
    if not json.loads(me_row["permissions"]).get("manageAdmins"):
        c.close(); raise HTTPException(403, "Yönetici ekleme yetkiniz yok")
    uname = body.username.strip().lower()
    if c.execute("SELECT 1 FROM admins WHERE username=?", (uname,)).fetchone():
        c.close(); raise HTTPException(400, "Bu kullanıcı adı zaten var")
    if c.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone():
        c.close(); raise HTTPException(400, "Bu kullanıcı adı zaten var")
    c.execute("INSERT INTO admins VALUES (?,?,?,?,?,?)",
              (uname, hash_password(body.password), body.displayName, 0,
               json.dumps(body.permissions), time.time()))
    c.commit(); c.close()
    return {"ok": True}

@app.delete("/api/admin/admins/{uname}")
def admin_delete(uname: str, s=Depends(require_admin)):
    c = db()
    me_row = c.execute("SELECT permissions FROM admins WHERE username=?", (s["username"],)).fetchone()
    if not json.loads(me_row["permissions"]).get("manageAdmins"):
        c.close(); raise HTTPException(403, "Yetkiniz yok")
    t = c.execute("SELECT is_super FROM admins WHERE username=?", (uname,)).fetchone()
    if not t: c.close(); raise HTTPException(404, "Yönetici yok")
    if t["is_super"]: c.close(); raise HTTPException(400, "Baş yönetici silinemez")
    c.execute("DELETE FROM admins WHERE username=?", (uname,))
    c.commit(); c.close()
    return {"ok": True}

# ═══════════════ PİYASA VERİSİ ═══════════════
BIST_SYMBOLS = ["THYAO","GARAN","AKBNK","ISCTR","SISE","EREGL","KCHOL","SAHOL","TUPRS","BIMAS",
    "ASELS","PETKM","PGSUS","TOASO","FROTO","ARCLK","VESTL","KOZAL","KOZAA","TCELL",
    "TTKOM","YKBNK","VAKBN","HALKB","SASA","HEKTS","KRDMD","ENKAI","EKGYO","MGROS",
    "ULKER","AEFES","CCOLA","SOKM","BRSAN","ALARK","OTKAR","OYAKC","TSKB","ISGYO",
    "ISMEN","DOHOL","GUBRF","ENJSA","ODAS","AGHOL","ZOREN","KONTR","KARSN","ASUZU",
    "CIMSA","BUCIM","ADANA","AKSA","AKSEN","ALKIM","ANHYT","ANSGR","BAGFS","BASGZ",
    "BERA","BFREN","BIOEN","BRISA","BTCIM","CANTE","CEMTS","CWENE","DOAS","ECILC",
    "EGEEN","EUPWR","GESAN","GOLTS","GSDHO","GWIND","HUNER","INDES","IPEKE","IZMDC",
    "KARTN","KLSER","KMPUR","KONYA","KORDS","KTLEV","LOGO","MAVI","MPARK","NTHOL",
    "NUGYO","ORGE","PENTA","PSGYO","QUAGR","SEKFK","SKBNK","SMRTG","SNGYO","TABGD",
    "TATGD","TKFEN","TMSN","TRGYO","TURSG","VESBE","YEOTK","ZRGYO"][:100]
US_SYMBOLS = ["AAPL","MSFT","GOOGL","GOOG","AMZN","NVDA","META","TSLA","BRK.B","JPM","V","UNH",
    "MA","HD","PG","XOM","CVX","ABBV","MRK","LLY","PEP","KO","COST","AVGO","WMT","MCD",
    "CSCO","ADBE","CRM","NFLX","INTC","AMD","QCOM","TXN","IBM","ORCL","NKE","DIS","BA",
    "CAT","GE","GS","MS","WFC","BAC","C","AXP","SBUX","PYPL","INTU","AMGN","GILD","BMY",
    "PFE","T","VZ","CMCSA","HON","UPS","RTX","LMT","DE","MMM","UNP","PM","MO","TMO",
    "ABT","DHR","MDT","SPGI","BLK","NOW"][:70]
INDEX_SYMBOLS = {"XU100":"XU100.IS","XU030":"XU030.IS","SPX":"^GSPC","DJI":"^DJI","NDX":"^NDX",
    "DAX":"^GDAXI","FTSE":"^FTSE","CAC":"^FCHI","N225":"^N225","HSI":"^HSI",
    "SHCOMP":"000001.SS","SX5E":"^STOXX50E","RUT":"^RUT","VIX":"^VIX","MXEF":"EEM"}
COMMODITY_SYMBOLS = {"XAU":"GC=F","XAG":"SI=F","XPT":"PL=F","XPD":"PA=F","WTI":"CL=F","BRENT":"BZ=F",
    "NATGAS":"NG=F","GASOLINE":"RB=F","WHEAT":"ZW=F","CORN":"ZC=F","SOYBEAN":"ZS=F",
    "COFFEE":"KC=F","COTTON":"CT=F","SUGAR":"SB=F","COCOA":"CC=F","COPPER":"HG=F","ALUMINUM":"ALI=F"}
CRYPTO_COINGECKO_IDS = {"BTC":"bitcoin","ETH":"ethereum","USDT":"tether","SOL":"solana","BNB":"binancecoin",
    "XRP":"ripple","ADA":"cardano","DOGE":"dogecoin","AVAX":"avalanche-2","DOT":"polkadot"}

cache: Dict[str, Any] = {}
CACHE_TTL = 300
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36", "Accept": "application/json"}

def is_cache_valid(k): return k in cache and (time.time() - cache[k]["ts"]) < CACHE_TTL
def set_cache(k, v): cache[k] = {"data": v, "ts": time.time()}
def get_cache(k): return cache.get(k, {}).get("data")

def fetch_yahoo_quote(y_sym, kx_id):
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{y_sym}",
                         params={"interval": "1d", "range": "5d"}, headers=HEADERS, timeout=5)
        if r.status_code != 200: return None
        res = r.json().get("chart", {}).get("result", [])
        if not res: return None
        closes = [c for c in res[0]["indicators"]["quote"][0]["close"] if c is not None]
        if not closes: return None
        price = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else price
        change_pct = ((price - prev) / prev * 100) if prev else 0
        return {"id": kx_id, "price": round(price, 4), "change_pct": round(change_pct, 2)}
    except Exception:
        return None

def fetch_yahoo_batch(symbol_map):
    results = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(fetch_yahoo_quote, y, kx): kx for kx, y in symbol_map.items()}
        for f in as_completed(futures):
            try:
                row = f.result(timeout=15)
                if row: results.append(row)
            except Exception: pass
    return results

def fetch_crypto_coingecko():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency":"usd","ids":",".join(CRYPTO_COINGECKO_IDS.values()),
                    "order":"market_cap_desc","per_page":50,"page":1,
                    "price_change_percentage":"24h"}, timeout=15)
        r.raise_for_status()
        by_id = {c["id"]: c for c in r.json()}
        out = []
        for kx, gid in CRYPTO_COINGECKO_IDS.items():
            c = by_id.get(gid)
            if c:
                out.append({"id": kx, "price": c["current_price"],
                            "change_pct": round(c.get("price_change_percentage_24h") or 0, 2)})
        return out
    except Exception:
        return []

@app.get("/api/kriptox/all")
async def get_all():
    async def fetch_or_cached(key, fn):
        if is_cache_valid(key): return get_cache(key)
        data = await asyncio.get_event_loop().run_in_executor(None, fn)
        set_cache(key, data)
        return data
    results = await asyncio.gather(
        fetch_or_cached("bist", lambda: fetch_yahoo_batch({s: f"{s}.IS" for s in BIST_SYMBOLS})),
        fetch_or_cached("us", lambda: fetch_yahoo_batch({s: ("BRK-B" if s == "BRK.B" else s) for s in US_SYMBOLS})),
        fetch_or_cached("indices", lambda: fetch_yahoo_batch(INDEX_SYMBOLS)),
        fetch_or_cached("commodities", lambda: fetch_yahoo_batch(COMMODITY_SYMBOLS)),
        fetch_or_cached("crypto", fetch_crypto_coingecko),
    )
    return {"bist": results[0], "us": results[1], "indices": results[2],
            "commodities": results[3], "crypto": results[4]}

@app.get("/")
def root():
    return {"message": "BorsaX API çalışıyor", "docs": "/docs"}

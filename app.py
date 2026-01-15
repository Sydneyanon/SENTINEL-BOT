"""
Sentinel Signals - ULTIMATE Edition - PART 1 of 2
Complete bot with ALL features integrated
"""

import os
import sys
import asyncio
import json
import time
import logging
import signal
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

import aiohttp
import aiosqlite
from aiogram import Bot
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from aiohttp import web
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import pickle
import anthropic
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

def signal_handler(signum, frame):
    logger = logging.getLogger("SentinelSignals")
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
load_dotenv()

# Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
DEXSCREENER_API = "https://api.dexscreener.com"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 45))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY_USD", 8000))
MIN_AGE_MINUTES = int(os.getenv("MIN_TOKEN_AGE_MINUTES", 10))
MAX_AGE_HOURS = int(os.getenv("MAX_TOKEN_AGE_HOURS", 12))
MAX_SIGNALS_PER_HOUR = int(os.getenv("MAX_SIGNALS_PER_HOUR", 2))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_BETWEEN_POSTS_SEC", 300))
DB_PATH = os.getenv("DATABASE_PATH", "./data/sentinel.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "./logs/sentinel.log")
HEALTHCHECK_PORT = int(os.getenv("PORT", 8080))

FOLLOWUP_CHECK_INTERVAL = int(os.getenv("FOLLOWUP_CHECK_INTERVAL_SEC", 300))
FOLLOWUP_TRACK_DURATION_HOURS = int(os.getenv("FOLLOWUP_TRACK_DURATION_HOURS", 48))
ALERT_VOLUME_DROP_PCT = float(os.getenv("ALERT_VOLUME_DROP_PERCENT", 50))
ALERT_LIQUIDITY_DROP_PCT = float(os.getenv("ALERT_LIQUIDITY_DROP_PERCENT", 60))
ALERT_VOLUME_SPIKE_PCT = float(os.getenv("ALERT_VOLUME_SPIKE_PERCENT", 200))
ALERT_PRICE_SPIKE_PCT = float(os.getenv("ALERT_PRICE_SPIKE_PERCENT", 100))
ALERT_PRICE_DROP_PCT = float(os.getenv("ALERT_PRICE_DROP_PERCENT", 50))

PERFORMANCE_CHECK_INTERVAL = int(os.getenv("PERFORMANCE_CHECK_INTERVAL_SEC", 1800))
PERFORMANCE_MILESTONES = [2.0, 3.0, 5.0]

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ENABLE_AI_ANALYSIS = os.getenv("ENABLE_AI_ANALYSIS", "false").lower() == "true"
AI_CONFIDENCE_BOOST = float(os.getenv("AI_CONFIDENCE_BOOST", 10))

ENABLE_ML_LEARNING = os.getenv("ENABLE_ML_LEARNING", "false").lower() == "true"
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "./data/conviction_model.pkl")
ML_SCALER_PATH = os.getenv("ML_SCALER_PATH", "./data/scaler.pkl")

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ENABLE_CONTRACT_SCANNER = os.getenv("ENABLE_CONTRACT_SCANNER", "false").lower() == "true"
ENABLE_NARRATIVE_TRACKER = os.getenv("ENABLE_NARRATIVE_TRACKER", "true").lower() == "true"
CONTRACT_SAFETY_WEIGHT = float(os.getenv("CONTRACT_SAFETY_WEIGHT", 15))
NARRATIVE_BOOST_WEIGHT = float(os.getenv("NARRATIVE_BOOST_WEIGHT", 8))
NARRATIVE_WINDOW_HOURS = int(os.getenv("NARRATIVE_WINDOW_HOURS", 24))

try:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
except:
    DB_PATH = "/tmp/sentinel.db"
    LOG_FILE = "/tmp/sentinel.log"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, mode='a')]
)
logger = logging.getLogger("SentinelSignals")

class AlertType(Enum):
    VOLUME_DROP = "volume_drop"
    LIQUIDITY_DROP = "liquidity_drop"
    VOLUME_SPIKE = "volume_spike"
    PRICE_SPIKE = "price_spike"
    PRICE_DROP = "price_drop"
    RUG_WARNING = "rug_warning"

@dataclass
class TokenData:
    address: str
    symbol: str
    name: str
    description: str = ""
    twitter: str = ""
    telegram: str = ""
    website: str = ""
    liquidity_usd: float = 0.0
    volume_24h: float = 0.0
    market_cap: float = 0.0
    price_usd: float = 0.0
    price_change_24h: float = 0.0
    txns_24h_buys: int = 0
    txns_24h_sells: int = 0
    launch_time: Optional[datetime] = None
    conviction_score: float = 0.0
    conviction_reasons: List[str] = field(default_factory=list)
    dex: str = ""
    pair_address: str = ""

@dataclass
class TokenSnapshot:
    address: str
    symbol: str
    name: str
    pair_address: str
    posted_at: datetime
    initial_liquidity: float
    initial_volume_24h: float
    initial_price: float
    conviction_score: float = 0.0
    current_liquidity: float = 0.0
    current_volume_24h: float = 0.0
    current_price: float = 0.0
    last_checked: Optional[datetime] = None
    alerts_sent: Set[AlertType] = field(default_factory=set)

class TokenDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db = None
    
    async def connect(self):
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("CREATE TABLE IF NOT EXISTS seen_tokens (address TEXT PRIMARY KEY, symbol TEXT, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, posted BOOLEAN DEFAULT 0, conviction_score REAL)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS signal_history (id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, symbol TEXT, posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, conviction_score REAL, initial_liquidity REAL)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS tracked_tokens (address TEXT PRIMARY KEY, symbol TEXT, name TEXT, pair_address TEXT, posted_at TIMESTAMP, conviction_score REAL, initial_liquidity REAL, initial_volume_24h REAL, initial_price REAL, current_liquidity REAL DEFAULT 0, current_volume_24h REAL DEFAULT 0, current_price REAL DEFAULT 0, last_checked TIMESTAMP, alerts_sent TEXT DEFAULT '[]')")
        await self.db.execute("CREATE TABLE IF NOT EXISTS alert_history (id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, alert_type TEXT, alert_sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metric_change_pct REAL, message TEXT)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS performance_tracking (id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, posted_at TIMESTAMP, initial_price REAL, price_1h REAL, price_6h REAL, price_24h REAL, max_multiple REAL DEFAULT 1.0, last_checked TIMESTAMP)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS ml_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, posted_at TIMESTAMP, peak_price REAL, final_outcome TEXT, gain_percent REAL, recorded_at TIMESTAMP)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS contract_scans (address TEXT PRIMARY KEY, mint_revoked BOOLEAN, freeze_revoked BOOLEAN, safety_score REAL, red_flags TEXT, scanned_at TIMESTAMP)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS narrative_tracking (id INTEGER PRIMARY KEY AUTOINCREMENT, narrative TEXT, token_address TEXT, performance REAL, tracked_at TIMESTAMP)")
        await self.db.commit()
        logger.info(f"Database initialized: {self.db_path}")
    
    async def is_seen(self, address: str) -> bool:
        cursor = await self.db.execute("SELECT 1 FROM seen_tokens WHERE address = ?", (address,))
        return await cursor.fetchone() is not None
    
    async def mark_seen(self, token: TokenData, posted: bool = False):
        await self.db.execute("INSERT OR REPLACE INTO seen_tokens (address, symbol, posted, conviction_score) VALUES (?, ?, ?, ?)", (token.address, token.symbol, posted, token.conviction_score))
        if posted:
            await self.db.execute("INSERT INTO signal_history (address, symbol, conviction_score, initial_liquidity) VALUES (?, ?, ?, ?)", (token.address, token.symbol, token.conviction_score, token.liquidity_usd))
        await self.db.commit()
    
    async def get_recent_posts_count(self, hours: int = 1) -> int:
        cutoff = datetime.now() - timedelta(hours=hours)
        cursor = await self.db.execute("SELECT COUNT(*) FROM signal_history WHERE posted_at > ?", (cutoff,))
        result = await cursor.fetchone()
        return result[0] if result else 0
    
    async def add_tracked_token(self, snapshot: TokenSnapshot):
        await self.db.execute("INSERT OR REPLACE INTO tracked_tokens (address, symbol, name, pair_address, posted_at, conviction_score, initial_liquidity, initial_volume_24h, initial_price, current_liquidity, current_volume_24h, current_price, last_checked, alerts_sent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot.address, snapshot.symbol, snapshot.name, snapshot.pair_address, snapshot.posted_at, snapshot.conviction_score, snapshot.initial_liquidity, snapshot.initial_volume_24h, snapshot.initial_price, snapshot.current_liquidity, snapshot.current_volume_24h, snapshot.current_price, snapshot.last_checked, json.dumps([])))
        await self.db.commit()
    
    async def get_active_tracked_tokens(self) -> List[TokenSnapshot]:
        cutoff = datetime.now() - timedelta(hours=FOLLOWUP_TRACK_DURATION_HOURS)
        cursor = await self.db.execute("SELECT address, symbol, name, pair_address, posted_at, conviction_score, initial_liquidity, initial_volume_24h, initial_price, current_liquidity, current_volume_24h, current_price, last_checked, alerts_sent FROM tracked_tokens WHERE posted_at > ?", (cutoff,))
        rows = await cursor.fetchall()
        snapshots = []
        for row in rows:
            alerts_sent = set()
            try: alerts_sent = {AlertType(a) for a in json.loads(row[13])}
            except: pass
            snapshots.append(TokenSnapshot(address=row[0], symbol=row[1], name=row[2], pair_address=row[3], posted_at=datetime.fromisoformat(row[4]), conviction_score=row[5], initial_liquidity=row[6], initial_volume_24h=row[7], initial_price=row[8], current_liquidity=row[9], current_volume_24h=row[10], current_price=row[11], last_checked=datetime.fromisoformat(row[12]) if row[12] else None, alerts_sent=alerts_sent))
        return snapshots
    
    async def update_tracked_token(self, snapshot: TokenSnapshot):
        alerts_json = json.dumps([a.value for a in snapshot.alerts_sent])
        await self.db.execute("UPDATE tracked_tokens SET current_liquidity = ?, current_volume_24h = ?, current_price = ?, last_checked = ?, alerts_sent = ? WHERE address = ?",
            (snapshot.current_liquidity, snapshot.current_volume_24h, snapshot.current_price, datetime.now(), alerts_json, snapshot.address))
        await self.db.commit()
    
    async def log_alert(self, address: str, alert_type: AlertType, change_pct: float, message: str):
        await self.db.execute("INSERT INTO alert_history (address, alert_type, metric_change_pct, message) VALUES (?, ?, ?, ?)", (address, alert_type.value, change_pct, message))
        await self.db.commit()
    
    async def cleanup_old_tracked_tokens(self):
        cutoff = datetime.now() - timedelta(hours=FOLLOWUP_TRACK_DURATION_HOURS)
        await self.db.execute("DELETE FROM tracked_tokens WHERE posted_at < ?", (cutoff,))
        await self.db.commit()
    
    async def add_performance_tracking(self, address: str, initial_price: float):
        await self.db.execute("INSERT OR IGNORE INTO performance_tracking (address, posted_at, initial_price, last_checked) VALUES (?, ?, ?, ?)", (address, datetime.now(), initial_price, datetime.now()))
        await self.db.commit()
    
    async def get_tracked_performance(self) -> List[dict]:
        cursor = await self.db.execute("SELECT address, posted_at, initial_price, max_multiple, last_checked FROM performance_tracking")
        rows = await cursor.fetchall()
        return [{"address": r[0], "posted_at": datetime.fromisoformat(r[1]) if r[1] else None, "initial_price": r[2], "max_multiple": r[3], "last_checked": datetime.fromisoformat(r[4]) if r[4] else None} for r in rows]
    
    async def update_max_multiple(self, address: str, current_multiple: float):
        await self.db.execute("UPDATE performance_tracking SET max_multiple = GREATEST(max_multiple, ?), last_checked = ? WHERE address = ?", (current_multiple, datetime.now(), address))
        await self.db.commit()
    
    async def get_contract_scan(self, address: str) -> Optional[Dict]:
        cursor = await self.db.execute("SELECT mint_revoked, freeze_revoked, safety_score, red_flags FROM contract_scans WHERE address = ? AND scanned_at > ?", (address, datetime.now() - timedelta(hours=24)))
        row = await cursor.fetchone()
        if row:
            return {"mint_revoked": bool(row[0]), "freeze_revoked": bool(row[1]), "safety_score": row[2], "red_flags": json.loads(row[3])}
        return None
    
    async def add_contract_scan(self, address: str, mint_revoked: bool, freeze_revoked: bool, safety_score: float, flags: List[str]):
        await self.db.execute("INSERT OR REPLACE INTO contract_scans (address, mint_revoked, freeze_revoked, safety_score, red_flags, scanned_at) VALUES (?, ?, ?, ?, ?, ?)", (address, mint_revoked, freeze_revoked, safety_score, json.dumps(flags), datetime.now()))
        await self.db.commit()
    
    async def track_narrative(self, narrative: str, token_address: str, performance: float):
        await self.db.execute("INSERT INTO narrative_tracking (narrative, token_address, performance, tracked_at) VALUES (?, ?, ?, ?)", (narrative, token_address, performance, datetime.now()))
        await self.db.commit()
    
    async def get_narrative_stats(self, hours: int = 24) -> Dict[str, Dict]:
        cutoff = datetime.now() - timedelta(hours=hours)
        cursor = await self.db.execute("SELECT narrative, COUNT(*) as count, AVG(performance) as avg_perf FROM narrative_tracking WHERE tracked_at > ? GROUP BY narrative ORDER BY count DESC", (cutoff,))
        rows = await cursor.fetchall()
        return {row[0]: {"count": row[1], "avg_performance": row[2]} for row in rows}
    
    async def get_win_rate_stats(self, hours: int = 168) -> Dict:
        """Get comprehensive win rate statistics"""
        cutoff = datetime.now() - timedelta(hours=hours)
        cursor = await self.db.execute("""
            SELECT t.address, t.symbol, t.posted_at, t.conviction_score, 
                   t.initial_price, t.current_price, p.max_multiple
            FROM tracked_tokens t
            LEFT JOIN performance_tracking p ON t.address = p.address
            WHERE t.posted_at > ?
        """, (cutoff,))
        rows = await cursor.fetchall()
        total = len(rows)
        if total == 0:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "avg_gain": 0, "best": {"symbol": "", "gain": 0}, "worst": {"symbol": "", "gain": 0}}
        wins = losses = 0
        total_gain = 0
        best = {"symbol": "", "gain": 0}
        worst = {"symbol": "", "gain": 0}
        for row in rows:
            symbol, max_multiple = row[1], row[6] or 1
            gain_pct = (max_multiple - 1) * 100
            total_gain += gain_pct
            if gain_pct >= 50: wins += 1
            else: losses += 1
            if gain_pct > best["gain"]: best = {"symbol": symbol, "gain": gain_pct}
            if worst["symbol"] == "" or gain_pct < worst["gain"]: worst = {"symbol": symbol, "gain": gain_pct}
        return {"total": total, "wins": wins, "losses": losses, "win_rate": (wins/total*100) if total > 0 else 0, "avg_gain": total_gain/total if total > 0 else 0, "best": best, "worst": worst}
    
    async def get_conviction_accuracy(self) -> Dict:
        """See if high conviction scores = better performance"""
        cursor = await self.db.execute("""
            SELECT conviction_score, max_multiple FROM tracked_tokens t
            LEFT JOIN performance_tracking p ON t.address = p.address
            WHERE max_multiple > 0
        """)
        rows = await cursor.fetchall()
        ranges = {"60-69": {"count": 0, "sum": 0}, "70-79": {"count": 0, "sum": 0}, "80-89": {"count": 0, "sum": 0}, "90-100": {"count": 0, "sum": 0}}
        for row in rows:
            score, multiple = row[0], row[1] or 1
            if 60 <= score < 70: ranges["60-69"]["count"] += 1; ranges["60-69"]["sum"] += multiple
            elif 70 <= score < 80: ranges["70-79"]["count"] += 1; ranges["70-79"]["sum"] += multiple
            elif 80 <= score < 90: ranges["80-89"]["count"] += 1; ranges["80-89"]["sum"] += multiple
            elif score >= 90: ranges["90-100"]["count"] += 1; ranges["90-100"]["sum"] += multiple
        for r in ranges:
            if ranges[r]["count"] > 0: ranges[r]["avg_multiple"] = ranges[r]["sum"] / ranges[r]["count"]
        return ranges

    async def close(self):
        if self.db:
            await self.db.close()

class ConvictionFilter:
    @staticmethod
    async def safety_check(token: TokenData) -> tuple[bool, str]:
        if not token.twitter and not token.telegram and not token.website:
            return False, "No social links"
        if token.liquidity_usd < MIN_LIQUIDITY:
            return False, f"Low liquidity"
        if token.launch_time:
            age_minutes = (datetime.now() - token.launch_time).total_seconds() / 60
            if age_minutes < MIN_AGE_MINUTES:
                return False, "Too new"
            if age_minutes / 60 > MAX_AGE_HOURS:
                return False, "Too old"
        return True, "Passed"
    
    @staticmethod
    async def calculate_conviction(token: TokenData) -> tuple[float, List[str]]:
        score, reasons = 0.0, []
        if token.twitter: score += 10; reasons.append("✓ Twitter")
        if token.telegram: score += 10; reasons.append("✓ Telegram")
        if token.website: score += 5; reasons.append("✓ Website")
        
        if token.liquidity_usd >= MIN_LIQUIDITY * 5:
            score += 25; reasons.append(f"💰 ${token.liquidity_usd:,.0f} liq")
        elif token.liquidity_usd >= MIN_LIQUIDITY * 3:
            score += 20; reasons.append(f"💰 ${token.liquidity_usd:,.0f} liq")
        elif token.liquidity_usd >= MIN_LIQUIDITY:
            score += 10; reasons.append(f"✓ ${token.liquidity_usd:,.0f} liq")
        
        if token.volume_24h > 0 and token.liquidity_usd > 0:
            ratio = token.volume_24h / token.liquidity_usd
            if ratio > 5: score += 25; reasons.append(f"🚀 ${token.volume_24h:,.0f} vol")
            elif ratio > 3: score += 20; reasons.append(f"🔥 ${token.volume_24h:,.0f} vol")
            elif ratio > 1.5: score += 15; reasons.append("✓ Strong vol")
        
        if token.price_change_24h > 100: score += 15; reasons.append(f"📈 +{token.price_change_24h:.0f}%")
        elif token.price_change_24h > 50: score += 12; reasons.append(f"📈 +{token.price_change_24h:.0f}%")
        elif token.price_change_24h > 20: score += 8; reasons.append(f"📈 +{token.price_change_24h:.0f}%")
        
        if token.txns_24h_buys > 0 and token.txns_24h_sells > 0:
            if token.txns_24h_buys / max(token.txns_24h_sells, 1) > 2:
                score += 10; reasons.append(f"🔥 Buy pressure")
        
        return min(score, 100), reasons

# PART 2 of 2 - Continue from Part 1
# Paste this AFTER Part 1 in the same file


# ML/AI/Scanner Classes
class MLLearningEngine:
    def __init__(self, db: TokenDatabase):
        self.db = db
        self.model = None
        self.scaler = None
    
    async def initialize(self):
        try:
            with open(ML_MODEL_PATH, 'rb') as f: self.model = pickle.load(f)
            with open(ML_SCALER_PATH, 'rb') as f: self.scaler = pickle.load(f)
            logger.info("✓ ML model loaded")
        except: self.model = RandomForestClassifier(n_estimators=100, max_depth=10); self.scaler = StandardScaler()
    
    async def adjust_conviction_with_ml(self, token: TokenData, base_score: float) -> Tuple[float, str]:
        if not self.model: return base_score, "ML model not ready"
        try:
            features = np.array([[token.conviction_score, token.liquidity_usd, token.volume_24h, len(token.symbol), token.volume_24h / max(token.liquidity_usd, 1)]])
            prob = self.model.predict_proba(self.scaler.transform(features))[0][1]
            adj = (prob - 0.5) * 20
            return max(0, min(100, base_score + adj)), f"ML: {prob*100:.0f}% win prob"
        except: return base_score, "ML error"

class AIAnalysisEngine:
    def __init__(self, api_key: str, db: TokenDatabase):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.db = db
    
    async def analyze_token(self, token: TokenData) -> Dict:
        prompt = f"Analyze memecoin:\nNAME: {token.name}\nDESC: {token.description[:200]}\nSOCIALS: TW:{'Y' if token.twitter else 'N'} TG:{'Y' if token.telegram else 'N'}\nLIQ: ${token.liquidity_usd:,.0f}\n\nRespond:\nRISK_SCORE: [0-100]\nCONFIDENCE_ADJUSTMENT: [-10 to +10]\nANALYSIS: [1-2 sentences]"
        try:
            msg = await asyncio.to_thread(self.client.messages.create, model="claude-sonnet-4-20250514", max_tokens=300, messages=[{"role": "user", "content": prompt}])
            text = msg.content[0].text
            result = {"risk_score": 50, "confidence_adjustment": 0, "analysis": ""}
            for line in text.split('\n'):
                if "RISK_SCORE:" in line: result["risk_score"] = float(line.split(':')[1].strip())
                elif "CONFIDENCE_ADJUSTMENT:" in line: result["confidence_adjustment"] = float(line.split(':')[1].strip())
                elif "ANALYSIS:" in line: result["analysis"] = line.split(':', 1)[1].strip()
            return result
        except Exception as e:
            logger.error(f"AI: {e}")
            return {"risk_score": 50, "confidence_adjustment": 0, "analysis": ""}

class ContractRiskScanner:
    def __init__(self, rpc_url: str, db: TokenDatabase):
        self.client = AsyncClient(rpc_url, commitment=Confirmed)
        self.db = db
    
    async def scan_token(self, address: str) -> Dict:
        cached = await self.db.get_contract_scan(address)
        if cached: return {**cached, "green_flags": []}
        try:
            pubkey = Pubkey.from_string(address)
            response = await self.client.get_account_info(pubkey)
            if not response.value: return {"safety_score": 0, "mint_revoked": False, "freeze_revoked": False, "red_flags": []}
            data = response.value.data
            mint_revoked = len(data) > 0 and data[0] == 0
            freeze_revoked = len(data) > 36 and data[36] == 0
            score = 50
            red_flags, green_flags = [], []
            if mint_revoked: score += 25; green_flags.append("✅ Mint revoked")
            else: score -= 15; red_flags.append("⚠️ Mint active")
            if freeze_revoked: score += 25; green_flags.append("✅ Freeze revoked")
            else: score -= 15; red_flags.append("⚠️ Freeze active")
            await self.db.add_contract_scan(address, mint_revoked, freeze_revoked, max(0, min(100, score)), red_flags)
            return {"safety_score": max(0, min(100, score)), "mint_revoked": mint_revoked, "freeze_revoked": freeze_revoked, "red_flags": red_flags, "green_flags": green_flags}
        except Exception as e:
            logger.error(f"Contract scan: {e}")
            return {"safety_score": 50, "mint_revoked": False, "freeze_revoked": False, "red_flags": [], "green_flags": []}
    
    async def close(self):
        await self.client.close()

class NarrativeTracker:
    NARRATIVES = {"cat": ["cat", "kitty", "meow"], "dog": ["dog", "doge", "shiba"], "ai": ["ai", "gpt", "bot"], "frog": ["frog", "pepe"], "political": ["trump", "biden"], "food": ["burger", "pizza"], "meme": ["chad", "wojak"], "anime": ["anime", "waifu"], "tech": ["elon", "rocket"], "moon": ["moon", "100x"]}
    
    def __init__(self, db: TokenDatabase):
        self.db = db
        self.cache = {}
    
    async def detect_narrative(self, token: TokenData) -> List[str]:
        text = f"{token.name} {token.symbol} {token.description}".lower()
        return [n for n, kws in self.NARRATIVES.items() if any(kw in text for kw in kws)]
    
    async def calculate_narrative_boost(self, token: TokenData) -> Tuple[float, List[str]]:
        narrs = await self.detect_narrative(token)
        if not narrs: return 0, []
        stats = await self.db.get_narrative_stats(NARRATIVE_WINDOW_HOURS)
        boost, reasons = 0, []
        for n in narrs:
            if n in stats and stats[n]["count"] >= 5 and stats[n]["avg_performance"] > 50:
                boost += NARRATIVE_BOOST_WEIGHT
                reasons.append(f"🔥 {n.upper()} meta HOT")
        return min(boost, NARRATIVE_BOOST_WEIGHT), reasons

# DexScreener Monitor
class DexScreenerMonitor:
    def __init__(self):
        self.session = None
        self.running = False
    
    async def start(self, callback):
        self.running = True
        self.session = aiohttp.ClientSession()
        logger.info("🟢 DexScreener monitor started")
        while self.running:
            try:
                await self._fetch_latest_profiles(callback)
                await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error(f"DexScreener: {e}")
                await asyncio.sleep(POLL_INTERVAL)
    
    async def _fetch_latest_profiles(self, callback):
        try:
            async with self.session.get(f"{DEXSCREENER_API}/token-profiles/latest/v1", timeout=15) as resp:
                if resp.status != 200: return
                profiles = await resp.json()
                if not isinstance(profiles, list): return
                solana = [p for p in profiles if p.get("chainId") == "solana"]
                for profile in solana[:15]:
                    addr = profile.get("tokenAddress")
                    if addr:
                        token = await self._fetch_token_pairs(addr, profile)
                        if token: await callback(token)
        except: pass
    
    async def _fetch_token_pairs(self, address: str, profile: dict) -> Optional[TokenData]:
        try:
            async with self.session.get(f"{DEXSCREENER_API}/latest/dex/tokens/{address}", timeout=10) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                pairs = data.get("pairs", [])
                if not pairs: return None
                pairs.sort(key=lambda x: float(x.get("liquidity", {}).get("usd", 0)), reverse=True)
                return await self._parse_pair(pairs[0], profile)
        except: return None
    
    async def _parse_pair(self, pair: dict, profile: dict = None) -> Optional[TokenData]:
        try:
            base = pair.get("baseToken", {})
            addr = base.get("address")
            if not addr: return None
            created = pair.get("pairCreatedAt")
            launch = datetime.fromtimestamp(created / 1000) if created else None
            pc = pair.get("priceChange", {})
            txns = pair.get("txns", {}).get("h24", {})
            tw = tg = web = desc = ""
            if profile:
                desc = profile.get("description", "")
                for link in profile.get("links", []):
                    lt, lu = link.get("type", "").lower(), link.get("url", "")
                    if "twitter" in lt or "x.com" in lu: tw = lu
                    elif "telegram" in lt or "t.me" in lu: tg = lu
                    elif "website" in lt: web = lu
            info = pair.get("info", {})
            if info:
                for s in info.get("socials", []):
                    st, su = s.get("type", "").lower(), s.get("url", "")
                    if "twitter" in st and not tw: tw = su
                    elif "telegram" in st and not tg: tg = su
                for w in info.get("websites", []):
                    if not web: web = w.get("url", "")
            return TokenData(address=addr, symbol=base.get("symbol", ""), name=base.get("name", ""), description=desc, twitter=tw, telegram=tg, website=web,
                liquidity_usd=float(pair.get("liquidity", {}).get("usd", 0)), volume_24h=float(pair.get("volume", {}).get("h24", 0)),
                market_cap=float(pair.get("fdv", 0)), price_usd=float(pair.get("priceUsd", 0)), price_change_24h=float(pc.get("h24", 0)),
                txns_24h_buys=int(txns.get("buys", 0)), txns_24h_sells=int(txns.get("sells", 0)), launch_time=launch, dex=pair.get("dexId", ""), pair_address=pair.get("pairAddress", ""))
        except: return None
    
    async def stop(self):
        self.running = False
        if self.session: await self.session.close()

# Follow-up Monitor
class TokenFollowUpMonitor:
    def __init__(self, db: TokenDatabase, publisher: 'TelegramPublisher', session: aiohttp.ClientSession):
        self.db, self.publisher, self.session, self.running = db, publisher, session, False
    
    async def start(self):
        self.running = True
        logger.info(f"🔍 Follow-up monitor started")
        while self.running:
            try:
                await self._check_tracked_tokens()
                await asyncio.sleep(FOLLOWUP_CHECK_INTERVAL)
                if datetime.now().minute == 0: await self.db.cleanup_old_tracked_tokens()
            except Exception as e:
                logger.error(f"Follow-up: {e}")
                await asyncio.sleep(FOLLOWUP_CHECK_INTERVAL)
    
    async def _check_tracked_tokens(self):
        tokens = await self.db.get_active_tracked_tokens()
        if not tokens: return
        for snap in tokens:
            try:
                data = await self._fetch_current(snap.pair_address or snap.address)
                if not data: continue
                snap.current_liquidity = data.get("liquidity", 0)
                snap.current_volume_24h = data.get("volume_24h", 0)
                snap.current_price = data.get("price", 0)
                snap.last_checked = datetime.now()
                alerts = await self._analyze(snap)
                for atype, pct, msg in alerts:
                    if atype not in snap.alerts_sent:
                        await self.publisher.publish_update(snap, atype, pct, msg)
                        snap.alerts_sent.add(atype)
                        await self.db.log_alert(snap.address, atype, pct, msg)
                await self.db.update_tracked_token(snap)
                await asyncio.sleep(1)
            except: pass
    
    async def _fetch_current(self, address: str) -> Optional[dict]:
        try:
            async with self.session.get(f"{DEXSCREENER_API}/latest/dex/tokens/{address}", timeout=10) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                pairs = data.get("pairs", [])
                if not pairs: return None
                pairs.sort(key=lambda x: float(x.get("liquidity", {}).get("usd", 0)), reverse=True)
                p = pairs[0]
                return {"liquidity": float(p.get("liquidity", {}).get("usd", 0)), "volume_24h": float(p.get("volume", {}).get("h24", 0)), "price": float(p.get("priceUsd", 0))}
        except: return None
    
    async def _analyze(self, s: TokenSnapshot) -> List[tuple]:
        def pct(i, c): return ((c - i) / i * 100) if i else 0
        lc, vc, pc = pct(s.initial_liquidity, s.current_liquidity), pct(s.initial_volume_24h, s.current_volume_24h), pct(s.initial_price, s.current_price)
        alerts = []
        if vc <= -ALERT_VOLUME_DROP_PCT: alerts.append((AlertType.VOLUME_DROP, vc, f"Vol -{abs(vc):.0f}%"))
        if lc <= -ALERT_LIQUIDITY_DROP_PCT: alerts.append((AlertType.LIQUIDITY_DROP, lc, f"Liq -{abs(lc):.0f}%"))
        if pc <= -ALERT_PRICE_DROP_PCT: alerts.append((AlertType.PRICE_DROP, pc, f"Price -{abs(pc):.0f}%"))
        if lc <= -40 and vc <= -40 and pc <= -30 and AlertType.RUG_WARNING not in s.alerts_sent:
            alerts.append((AlertType.RUG_WARNING, lc, f"⚠️ RUG: L{lc:.0f}% V{vc:.0f}% P{pc:.0f}%"))
        if vc >= ALERT_VOLUME_SPIKE_PCT: alerts.append((AlertType.VOLUME_SPIKE, vc, f"Vol +{vc:.0f}%"))
        if pc >= ALERT_PRICE_SPIKE_PCT: alerts.append((AlertType.PRICE_SPIKE, pc, f"Price +{pc:.0f}% 🚀"))
        return alerts
    
    async def stop(self):
        self.running = False

# Performance Tracker
class PerformanceTracker:
    def __init__(self, db: TokenDatabase, publisher: 'TelegramPublisher', session: aiohttp.ClientSession):
        self.db, self.publisher, self.session, self.running = db, publisher, session, False
    
    async def start(self):
        self.running = True
        logger.info("📊 Performance tracker started")
        while self.running:
            try:
                await self._check_performance()
                await asyncio.sleep(PERFORMANCE_CHECK_INTERVAL)
            except: await asyncio.sleep(PERFORMANCE_CHECK_INTERVAL)
    
    async def _check_performance(self):
        tracked = await self.db.get_tracked_performance()
        for entry in tracked:
            try:
                data = await self._fetch_price(entry["address"])
                if not data: continue
                curr_price = data.get("price", 0)
                if curr_price <= 0 or entry["initial_price"] <= 0: continue
                mult = curr_price / entry["initial_price"]
                await self.db.update_max_multiple(entry["address"], mult)
                for milestone in PERFORMANCE_MILESTONES:
                    if mult >= milestone and (entry["max_multiple"] or 0) < milestone:
                        await self.publisher.publish_milestone(entry["address"], milestone, mult)
            except: pass
    
    async def _fetch_price(self, address: str) -> Optional[dict]:
        try:
            async with self.session.get(f"{DEXSCREENER_API}/latest/dex/tokens/{address}", timeout=10) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                pairs = data.get("pairs", [])
                if not pairs: return None
                return {"price": float(pairs[0].get("priceUsd", 0))}
        except: return None

class OutcomeTracker:
    #Tracks outcomes to feed ML training - Makes ML learn over time!
    def __init__(self, db: TokenDatabase, ml_engine: Optional['MLLearningEngine']):
        self.db = db
        self.ml_engine = ml_engine
        self.running = False
    
    async def start(self):
        self.running = True
        logger.info("📊 Outcome tracker started (feeds ML)")
        while self.running:
            try:
                await self._check_outcomes()
                await asyncio.sleep(3600)
                if self.ml_engine: await self.ml_engine.train_model()
            except Exception as e:
                logger.error(f"Outcome tracker: {e}")
                await asyncio.sleep(3600)
    
    async def _check_outcomes(self):
        cutoff_min = datetime.now() - timedelta(hours=48)
        cutoff_max = datetime.now() - timedelta(hours=24)
        cursor = await self.db.db.execute("""
            SELECT t.address, t.initial_price, t.posted_at, p.max_multiple
            FROM tracked_tokens t
            LEFT JOIN performance_tracking p ON t.address = p.address
            WHERE t.posted_at BETWEEN ? AND ?
            AND t.address NOT IN (SELECT address FROM ml_outcomes)
        """, (cutoff_min, cutoff_max))
        rows = await cursor.fetchall()
        for row in rows:
            address, initial_price, posted_at, max_multiple = row
            if not initial_price or not max_multiple: continue
            gain_pct = (max_multiple - 1) * 100
            outcome = "success" if gain_pct >= 50 else "failure"
            await self.db.db.execute("INSERT INTO ml_outcomes (address, posted_at, peak_price, final_outcome, gain_percent, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (address, posted_at, initial_price * max_multiple, outcome, gain_pct, datetime.now()))
            await self.db.db.commit()
            logger.info(f"📊 Outcome: {address[:8]} = {outcome} ({gain_pct:+.0f}%)")    

class WeeklyPerformanceReporter:
    #Auto-posts weekly performance reports to Telegram every Sunday
    def __init__(self, db: TokenDatabase, publisher: 'TelegramPublisher'):
        self.db = db
        self.publisher = publisher
        self.running = False
    
    async def start(self):
        self.running = True
        logger.info("📊 Weekly reporter started")
        while self.running:
            try:
                now = datetime.now()
                if now.weekday() == 6 and now.hour == 0:
                    await self.post_weekly_report()
                    await asyncio.sleep(3600)
                else:
                    await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Weekly reporter: {e}")
                await asyncio.sleep(3600)
    
    async def post_weekly_report(self):
        logger.info("📊 Generating weekly report...")
        stats = await self.db.get_win_rate_stats(168)
        day = await self.db.get_win_rate_stats(24)
        conviction = await self.db.get_conviction_accuracy()
        if stats["total"] == 0: return
        emoji = "🔥🔥🔥" if stats["win_rate"] >= 70 else "🔥🔥" if stats["win_rate"] >= 50 else "🔥"
        msg = f"""{emoji} <b>WEEKLY PERFORMANCE REPORT</b> {emoji}

<b>📊 7-Day Stats:</b>
Total: {stats["total"]} | Wins: {stats["wins"]} | Losses: {stats["losses"]}
Win Rate: <b>{stats["win_rate"]:.1f}%</b>
Avg Gain: <b>{stats["avg_gain"]:+.1f}%</b>

<b>🏆 Best:</b> ${stats["best"]["symbol"]} (+{stats["best"]["gain"]:.0f}%)
<b>📉 Worst:</b> ${stats["worst"]["symbol"]} ({stats["worst"]["gain"]:+.0f}%)

<b>⚡ Last 24h:</b> {day["total"]} signals, {day["win_rate"]:.0f}% win rate

<b>🎯 Conviction Accuracy:</b>"""
        for r, d in conviction.items():
            if d["count"] > 0:
                avg = (d.get("avg_multiple", 1) - 1) * 100
                msg += f"\n{r}: {d['count']} signals, avg {avg:+.0f}%"
        msg += "\n\n📈 More signals coming!"
        await self.publisher.publish_weekly_report(msg)
        logger.info("✓ Weekly report posted")
        
    async def stop(self):
        self.running = False

# Telegram Publisher
class TelegramPublisher:
    def __init__(self, bot_token: str, channel_id: str):
        self.bot = Bot(token=bot_token)
        self.channel_id = channel_id
        self.last_post_time = 0
    
    async def publish_signal(self, token: TokenData):
        if time.time() - self.last_post_time < COOLDOWN_SEC:
            await asyncio.sleep(COOLDOWN_SEC - (time.time() - self.last_post_time))
        emoji = "🔥🔥🔥" if token.conviction_score >= 80 else "🔥🔥" if token.conviction_score >= 65 else "🔥"
        socials = []
        if token.twitter: socials.append(f"<a href='{token.twitter}'>Twitter</a>")
        if token.telegram: socials.append(f"<a href='{token.telegram}'>Telegram</a>")
        if token.website: socials.append(f"<a href='{token.website}'>Website</a>")
        slinks = " | ".join(socials) if socials else "N/A"
        dex = f"https://dexscreener.com/solana/{token.pair_address or token.address}"
        reasons = "\n".join([f"  • {r}" for r in token.conviction_reasons[:6]])
        age = ""
        if token.launch_time:
            hrs = (datetime.now() - token.launch_time).total_seconds() / 3600
            age = f"⚡ &lt;{int(hrs * 60)}m old" if hrs < 1 else f"🕐 ~{int(hrs)}h old"
        msg = f"""{emoji} <b>HIGH CONVICTION SIGNAL</b> {emoji}

<b>{token.name}</b> (${token.symbol})
<b>CA:</b> <code>{token.address}</code>
<b>Score:</b> {token.conviction_score:.0f}/100

<b>Why This Could Smash:</b>
{reasons}

<b>Socials:</b> {slinks}
<b>Chart:</b> <a href='{dex}'>DexScreener</a>
<b>DEX:</b> {token.dex.upper()}
<b>Liq:</b> ${token.liquidity_usd:,.0f} | <b>Vol:</b> ${token.volume_24h:,.0f}
<b>24h:</b> {token.price_change_24h:+.1f}%
{age}

⚠️ DYOR - Not financial advice"""
        try:
            await self.bot.send_message(chat_id=self.channel_id, text=msg, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
            self.last_post_time = time.time()
            logger.info(f"✓ Posted: {token.symbol} ({token.conviction_score:.0f})")
        except Exception as e:
            logger.error(f"Post failed: {e}")
    
    async def publish_update(self, s: TokenSnapshot, atype: AlertType, pct: float, msg: str):
        emoji = "🚨" if atype in [AlertType.RUG_WARNING, AlertType.LIQUIDITY_DROP] else "⚠️" if atype in [AlertType.VOLUME_DROP, AlertType.PRICE_DROP] else "🚀"
        td = datetime.now() - s.posted_at
        hrs, mins = int(td.total_seconds() / 3600), int((td.total_seconds() % 3600) / 60)
        tstr = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"
        def pc(i, c): return ((c - i) / i * 100) if i else 0
        fmsg = f"""{emoji} <b>FOLLOW-UP: ${s.symbol}</b> {emoji}

<b>{msg}</b>

Time: {tstr} ago | Score: {s.conviction_score:.0f}/100

<b>Changes:</b>
Liq: {pc(s.initial_liquidity, s.current_liquidity):+.1f}%
Vol: {pc(s.initial_volume_24h, s.current_volume_24h):+.1f}%
Price: {pc(s.initial_price, s.current_price):+.1f}%

<a href='https://dexscreener.com/solana/{s.pair_address or s.address}'>Chart</a>"""
        try:
            await self.bot.send_message(chat_id=self.channel_id, text=fmsg, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
        except: pass
    
    async def publish_milestone(self, address: str, milestone: float, mult: float):
        msg = f"🚀 <b>MILESTONE: {milestone:.0f}× ACHIEVED!</b> 🚀\n\nToken: {address[:8]}...\nMultiple: {mult:.1f}×\n\n<a href='https://dexscreener.com/solana/{address}'>Chart</a>"
        try:
            await self.bot.send_message(chat_id=self.channel_id, text=msg, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
        except: pass

async def healthcheck_server():
    async def health(request): return web.Response(text="OK", status=200)
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/', health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HEALTHCHECK_PORT)
    await site.start()
    logger.info(f"✓ Healthcheck on :{HEALTHCHECK_PORT}")

async def publish_weekly_report(self, message: str):
        """Post weekly performance report"""
        try:
            await self.bot.send_message(chat_id=self.channel_id, text=message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            logger.info("✓ Weekly report posted")
        except Exception as e:
            logger.error(f"Weekly report failed: {e}")

# Main Orchestrator
class SentinelSignals:
    def __init__(self):
        self.db = TokenDatabase(DB_PATH)
        self.filter_engine = ConvictionFilter()
        self.publisher = TelegramPublisher(TELEGRAM_TOKEN, CHANNEL_ID)
        self.monitor = DexScreenerMonitor()
        self.followup_monitor = None
        self.performance_tracker = None
        self.ml_engine = MLLearningEngine(self.db) if ENABLE_ML_LEARNING else None
        self.ai_engine = AIAnalysisEngine(ANTHROPIC_API_KEY, self.db) if (ENABLE_AI_ANALYSIS and ANTHROPIC_API_KEY) else None
        self.contract_scanner = ContractRiskScanner(SOLANA_RPC_URL, self.db) if ENABLE_CONTRACT_SCANNER else None
        self.narrative_tracker = NarrativeTracker(self.db) if ENABLE_NARRATIVE_TRACKER else None
        self.running = False
    
    async def start(self):
        logger.info("=" * 60)
        logger.info("SENTINEL SIGNALS - ULTIMATE Edition")
        logger.info("=" * 60)
        await self.db.connect()
        if self.ml_engine: await self.ml_engine.initialize()
        self.followup_monitor = TokenFollowUpMonitor(self.db, self.publisher, self.monitor.session)
        self.performance_tracker = PerformanceTracker(self.db, self.publisher, self.monitor.session)
        self.running = True
        tasks = [
            asyncio.create_task(self.monitor.start(self.process_token)),
            asyncio.create_task(self.followup_monitor.start()),
            asyncio.create_task(self.performance_tracker.start()),
            asyncio.create_task(healthcheck_server()),
        ]
        logger.info("✓ All systems operational")
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            await self.stop()
    
    async def process_token(self, token: TokenData):
        if await self.db.is_seen(token.address): return
        logger.info(f"New: {token.symbol}")
        safe, reason = await self.filter_engine.safety_check(token)
        if not safe:
            await self.db.mark_seen(token, False)
            return
        score, reasons = await self.filter_engine.calculate_conviction(token)
        
        # ML boost
        if self.ml_engine:
            try:
                ml_score, ml_reason = await self.ml_engine.adjust_conviction_with_ml(token, score)
                if abs(ml_score - score) >= 2: reasons.append(f"🤖 {ml_reason}"); score = ml_score
            except: pass
        
        # AI boost
        if self.ai_engine:
            try:
                ai = await self.ai_engine.analyze_token(token)
                if ai["confidence_adjustment"] != 0:
                    score += ai["confidence_adjustment"]
                    score = max(0, min(100, score))
                    if ai["confidence_adjustment"] > 0: reasons.append(f"🧠 AI: {ai['analysis'][:60]}")
                    else: reasons.insert(0, f"⚠️ AI: {ai['analysis'][:60]}")
            except: pass
        
        # Contract scan
        if self.contract_scanner:
            try:
                scan = await self.contract_scanner.scan_token(token.address)
                adj = ((scan["safety_score"] - 50) / 50) * CONTRACT_SAFETY_WEIGHT
                score += adj
                score = max(0, min(100, score))
                if scan["mint_revoked"] and scan["freeze_revoked"]: reasons.append("🛡️ Safe contract")
                elif scan["red_flags"]: reasons.insert(0, f"⚠️ {scan['red_flags'][0]}")
            except: pass
        
        # Narrative boost
        if self.narrative_tracker:
            try:
                boost, narr_reasons = await self.narrative_tracker.calculate_narrative_boost(token)
                if boost > 0: score += boost; score = max(0, min(100, score)); reasons.extend(narr_reasons)
            except: pass
        
        token.conviction_score = score
        token.conviction_reasons = reasons
        logger.info(f"  ✓ {score:.0f}/100")
        
        if score < 60:
            await self.db.mark_seen(token, False)
            return
        
        if await self.db.get_recent_posts_count() >= MAX_SIGNALS_PER_HOUR:
            await self.db.mark_seen(token, False)
            return
        
        try:
            await self.publisher.publish_signal(token)
            await self.db.mark_seen(token, True)
            snapshot = TokenSnapshot(address=token.address, symbol=token.symbol, name=token.name, pair_address=token.pair_address,
                posted_at=datetime.now(), initial_liquidity=token.liquidity_usd, initial_volume_24h=token.volume_24h,
                initial_price=token.price_usd, conviction_score=token.conviction_score)
            await self.db.add_tracked_token(snapshot)
            await self.db.add_performance_tracking(token.address, token.price_usd)
            logger.info(f"  🚀 POSTED")
        except Exception as e:
            logger.error(f"Publish failed: {e}")
    
    async def stop(self):
        logger.info("Stopping...")
        self.running = False
        await self.monitor.stop()
        if self.followup_monitor: await self.followup_monitor.stop()
        if self.performance_tracker: await self.performance_tracker.stop()
        if self.contract_scanner: await self.contract_scanner.close()
        await self.db.close()
        logger.info("✓ Stopped")

async def main():
    if not all([TELEGRAM_TOKEN, CHANNEL_ID]):
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
        sys.exit(1)
    logger.info("✓ Config validated")
    sentinel = SentinelSignals()
    await sentinel.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal: {e}", exc_info=True)
        sys.exit(1)

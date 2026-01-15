"""
Sentinel Signals - Full Integrated Version (Part 1/2)
Copy this first, then append Part 2 below it
"""

import os
import sys
import asyncio
import json
import time
import logging
import signal
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
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
import base58
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
import re
from collections import defaultdict, Counter

def signal_handler(signum, frame):
    logger = logging.getLogger("SentinelSignals")
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

load_dotenv()

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

# Follow-up Monitoring
FOLLOWUP_CHECK_INTERVAL = int(os.getenv("FOLLOWUP_CHECK_INTERVAL_SEC", 300))
FOLLOWUP_TRACK_DURATION_HOURS = int(os.getenv("FOLLOWUP_TRACK_DURATION_HOURS", 48))

# Alert thresholds
ALERT_VOLUME_DROP_PCT = float(os.getenv("ALERT_VOLUME_DROP_PERCENT", 50))
ALERT_LIQUIDITY_DROP_PCT = float(os.getenv("ALERT_LIQUIDITY_DROP_PERCENT", 60))
ALERT_VOLUME_SPIKE_PCT = float(os.getenv("ALERT_VOLUME_SPIKE_PERCENT", 200))
ALERT_PRICE_SPIKE_PCT = float(os.getenv("ALERT_PRICE_SPIKE_PERCENT", 100))
ALERT_PRICE_DROP_PCT = float(os.getenv("ALERT_PRICE_DROP_PERCENT", 50))

# Performance Tracking
PERFORMANCE_CHECK_INTERVAL = int(os.getenv("PERFORMANCE_CHECK_INTERVAL_SEC", 1800))
PERFORMANCE_MILESTONES = [2.0, 3.0, 5.0]
PERFORMANCE_DRAWDOWN_ALERT = -50.0
WEEKLY_SUMMARY_HOUR = 0

# AI/ML/Scanner Config
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ENABLE_AI_ANALYSIS = os.getenv("ENABLE_AI_ANALYSIS", "true").lower() == "true"
AI_CONFIDENCE_BOOST = float(os.getenv("AI_CONFIDENCE_BOOST", 10))
ENABLE_ML_LEARNING = os.getenv("ENABLE_ML_LEARNING", "true").lower() == "true"
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "./data/conviction_model.pkl")
ML_SCALER_PATH = os.getenv("ML_SCALER_PATH", "./data/scaler.pkl")
ML_MIN_TRAINING_SAMPLES = int(os.getenv("ML_MIN_TRAINING_SAMPLES", 50))
ML_RETRAIN_INTERVAL_HOURS = int(os.getenv("ML_RETRAIN_INTERVAL_HOURS", 24))
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ENABLE_CONTRACT_SCANNER = os.getenv("ENABLE_CONTRACT_SCANNER", "true").lower() == "true"
ENABLE_NARRATIVE_TRACKER = os.getenv("ENABLE_NARRATIVE_TRACKER", "true").lower() == "true"
ENABLE_DEV_TRACKER = os.getenv("ENABLE_DEV_TRACKER", "true").lower() == "true"
CONTRACT_SAFETY_WEIGHT = float(os.getenv("CONTRACT_SAFETY_WEIGHT", 15))
NARRATIVE_BOOST_WEIGHT = float(os.getenv("NARRATIVE_BOOST_WEIGHT", 8))
NARRATIVE_WINDOW_HOURS = int(os.getenv("NARRATIVE_WINDOW_HOURS", 24))
DEV_MIN_SUCCESSFUL_LAUNCHES = int(os.getenv("DEV_MIN_SUCCESSFUL_LAUNCHES", 2))

try:
    db_dir = Path(DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"Warning: Failed to create directories: {e}")
    DB_PATH = "/tmp/sentinel.db"
    LOG_FILE = "/tmp/sentinel.log"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='a')
    ]
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
    price_change_1h: float = 0.0
    txns_24h_buys: int = 0
    txns_24h_sells: int = 0
    source: str = ""
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
        self.db: Optional[aiosqlite.Connection] = None
    
    async def connect(self):
        self.db = await aiosqlite.connect(self.db_path)
        
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS seen_tokens (
                address TEXT PRIMARY KEY,
                symbol TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                posted BOOLEAN DEFAULT 0,
                conviction_score REAL
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT,
                symbol TEXT,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                conviction_score REAL,
                initial_liquidity REAL,
                FOREIGN KEY (address) REFERENCES seen_tokens(address)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_tokens (
                address TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                pair_address TEXT,
                posted_at TIMESTAMP,
                conviction_score REAL,
                initial_liquidity REAL,
                initial_volume_24h REAL,
                initial_price REAL,
                current_liquidity REAL DEFAULT 0,
                current_volume_24h REAL DEFAULT 0,
                current_price REAL DEFAULT 0,
                last_checked TIMESTAMP,
                alerts_sent TEXT DEFAULT '[]',
                FOREIGN KEY (address) REFERENCES seen_tokens(address)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT,
                alert_type TEXT,
                alert_sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metric_change_pct REAL,
                message TEXT,
                FOREIGN KEY (address) REFERENCES tracked_tokens(address)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS performance_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT,
                posted_at TIMESTAMP,
                initial_price REAL,
                price_1h REAL DEFAULT NULL,
                price_6h REAL DEFAULT NULL,
                price_24h REAL DEFAULT NULL,
                max_multiple REAL DEFAULT 1.0,
                last_checked TIMESTAMP,
                FOREIGN KEY (address) REFERENCES tracked_tokens(address)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS ml_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT,
                posted_at TIMESTAMP,
                peak_price REAL,
                peak_time TIMESTAMP,
                final_outcome TEXT,
                gain_percent REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (address) REFERENCES tracked_tokens(address)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS ai_analysis_cache (
                address TEXT PRIMARY KEY,
                analysis_text TEXT,
                risk_score REAL,
                confidence_adjustment REAL,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS contract_scans (
                address TEXT PRIMARY KEY,
                mint_revoked BOOLEAN,
                freeze_revoked BOOLEAN,
                safety_score REAL,
                red_flags TEXT,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS narrative_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                narrative TEXT,
                token_address TEXT,
                performance REAL,
                tracked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS dev_scans (
                dev_wallet TEXT PRIMARY KEY,
                total_launches INTEGER,
                successful_launches INTEGER,
                rugs INTEGER,
                trust_score REAL,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.db.commit()
        logger.info(f"Database initialized: {self.db_path}")
    
    async def is_seen(self, address: str) -> bool:
        cursor = await self.db.execute("SELECT 1 FROM seen_tokens WHERE address = ?", (address,))
        result = await cursor.fetchone()
        return result is not None
    
    async def mark_seen(self, token: TokenData, posted: bool = False):
        await self.db.execute("""
            INSERT OR REPLACE INTO seen_tokens 
            (address, symbol, posted, conviction_score)
            VALUES (?, ?, ?, ?)
        """, (token.address, token.symbol, posted, token.conviction_score))
        if posted:
            await self.db.execute("""
                INSERT INTO signal_history 
                (address, symbol, conviction_score, initial_liquidity)
                VALUES (?, ?, ?, ?)
            """, (token.address, token.symbol, token.conviction_score, token.liquidity_usd))
        await self.db.commit()
    
    async def get_recent_posts_count(self, hours: int = 1) -> int:
        cutoff = datetime.now() - timedelta(hours=hours)
        cursor = await self.db.execute("SELECT COUNT(*) FROM signal_history WHERE posted_at > ?", (cutoff,))
        result = await cursor.fetchone()
        return result[0] if result else 0
    
    async def add_tracked_token(self, snapshot: TokenSnapshot):
        await self.db.execute("""
            INSERT OR REPLACE INTO tracked_tokens (
                address, symbol, name, pair_address, posted_at, conviction_score,
                initial_liquidity, initial_volume_24h, initial_price,
                current_liquidity, current_volume_24h, current_price,
                last_checked, alerts_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.address, snapshot.symbol, snapshot.name, snapshot.pair_address,
            snapshot.posted_at, snapshot.conviction_score,
            snapshot.initial_liquidity, snapshot.initial_volume_24h, snapshot.initial_price,
            snapshot.current_liquidity, snapshot.current_volume_24h, snapshot.current_price,
            snapshot.last_checked, json.dumps([])
        ))
        await self.db.commit()
        logger.info(f"✓ Added {snapshot.symbol} to follow-up tracking")
    
    async def get_active_tracked_tokens(self) -> List[TokenSnapshot]:
        cutoff = datetime.now() - timedelta(hours=FOLLOWUP_TRACK_DURATION_HOURS)
        cursor = await self.db.execute("""
            SELECT address, symbol, name, pair_address, posted_at, conviction_score,
                   initial_liquidity, initial_volume_24h, initial_price,
                   current_liquidity, current_volume_24h, current_price,
                   last_checked, alerts_sent
            FROM tracked_tokens
            WHERE posted_at > ?
        """, (cutoff,))
        rows = await cursor.fetchall()
        snapshots = []
        for row in rows:
            alerts_sent = set()
            try:
                alerts_json = json.loads(row[13])
                alerts_sent = {AlertType(a) for a in alerts_json}
            except:
                pass
            snapshot = TokenSnapshot(
                address=row[0], symbol=row[1], name=row[2], pair_address=row[3],
                posted_at=datetime.fromisoformat(row[4]), conviction_score=row[5],
                initial_liquidity=row[6], initial_volume_24h=row[7], initial_price=row[8],
                current_liquidity=row[9], current_volume_24h=row[10], current_price=row[11],
                last_checked=datetime.fromisoformat(row[12]) if row[12] else None,
                alerts_sent=alerts_sent
            )
            snapshots.append(snapshot)
        return snapshots
    
    async def update_tracked_token(self, snapshot: TokenSnapshot):
        alerts_json = json.dumps([a.value for a in snapshot.alerts_sent])
        await self.db.execute("""
            UPDATE tracked_tokens
            SET current_liquidity = ?, current_volume_24h = ?, current_price = ?,
                last_checked = ?, alerts_sent = ?
            WHERE address = ?
        """, (
            snapshot.current_liquidity, snapshot.current_volume_24h, snapshot.current_price,
            datetime.now(), alerts_json, snapshot.address
        ))
        await self.db.commit()
    
    async def log_alert(self, address: str, alert_type: AlertType, change_pct: float, message: str):
        await self.db.execute("""
            INSERT INTO alert_history (address, alert_type, metric_change_pct, message)
            VALUES (?, ?, ?, ?)
        """, (address, alert_type.value, change_pct, message))
        await self.db.commit()
    
    async def cleanup_old_tracked_tokens(self):
        cutoff = datetime.now() - timedelta(hours=FOLLOWUP_TRACK_DURATION_HOURS)
        await self.db.execute("DELETE FROM tracked_tokens WHERE posted_at < ?", (cutoff,))
        await self.db.commit()
        logger.debug("Cleaned up old tracked tokens")
    
    async def add_outcome_data(self, address: str, peak_price: float, peak_time: datetime, 
                               final_outcome: str, gain_percent: float):
        await self.db.execute("""
            INSERT INTO ml_outcomes (address, posted_at, peak_price, peak_time, 
                                   final_outcome, gain_percent, recorded_at)
            VALUES (?, (SELECT posted_at FROM tracked_tokens WHERE address = ?), ?, ?, ?, ?, ?)
        """, (address, address, peak_price, peak_time, final_outcome, gain_percent, datetime.now()))
        await self.db.commit()
    
    async def get_training_data(self, limit: int = 500) -> List[Dict]:
        cursor = await self.db.execute("""
            SELECT t.conviction_score, t.initial_liquidity, t.initial_volume_24h,
                   s.symbol, o.gain_percent, o.final_outcome
            FROM ml_outcomes o
            JOIN tracked_tokens t ON o.address = t.address
            JOIN seen_tokens s ON o.address = s.address
            ORDER BY o.recorded_at DESC LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [{"conviction_score": r[0], "liquidity": r[1], "volume": r[2],
                "symbol": r[3], "gain_percent": r[4], "success": 1 if r[5] == "success" else 0}
                for r in rows]
    
    async def get_contract_scan(self, address: str) -> Optional[Dict]:
        cursor = await self.db.execute("""
            SELECT mint_revoked, freeze_revoked, safety_score, red_flags
            FROM contract_scans WHERE address = ? AND scanned_at > ?
        """, (address, datetime.now() - timedelta(hours=24)))
        row = await cursor.fetchone()
        if row:
            return {"mint_revoked": bool(row[0]), "freeze_revoked": bool(row[1]),
                   "safety_score": row[2], "red_flags": json.loads(row[3])}
        return None
    
    async def add_contract_scan(self, address: str, mint_revoked: bool, freeze_revoked: bool,
                               safety_score: float, flags: List[str]):
        await self.db.execute("""
            INSERT OR REPLACE INTO contract_scans 
            (address, mint_revoked, freeze_revoked, safety_score, red_flags, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (address, mint_revoked, freeze_revoked, safety_score, json.dumps(flags), datetime.now()))
        await self.db.commit()
    
    async def track_narrative(self, narrative: str, token_address: str, performance: float):
        await self.db.execute("""
            INSERT INTO narrative_tracking (narrative, token_address, performance, tracked_at)
            VALUES (?, ?, ?, ?)
        """, (narrative, token_address, performance, datetime.now()))
        await self.db.commit()
    
    async def get_narrative_stats(self, hours: int = 24) -> Dict[str, Dict]:
        cutoff = datetime.now() - timedelta(hours=hours)
        cursor = await self.db.execute("""
            SELECT narrative, COUNT(*) as count, AVG(performance) as avg_perf, MAX(performance) as max_perf
            FROM narrative_tracking WHERE tracked_at > ?
            GROUP BY narrative ORDER BY count DESC
        """, (cutoff,))
        rows = await cursor.fetchall()
        return {row[0]: {"count": row[1], "avg_performance": row[2], "max_performance": row[3]}
                for row in rows}
    
    async def add_dev_scan(self, dev_wallet: str, total_launches: int, successful_launches: int,
                          rugs: int, trust_score: float):
        await self.db.execute("""
            INSERT OR REPLACE INTO dev_scans
            (dev_wallet, total_launches, successful_launches, rugs, trust_score, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (dev_wallet, total_launches, successful_launches, rugs, trust_score, datetime.now()))
        await self.db.commit()
    
    async def get_dev_scan(self, dev_wallet: str) -> Optional[Dict]:
        cursor = await self.db.execute("""
            SELECT total_launches, successful_launches, rugs, trust_score
            FROM dev_scans WHERE dev_wallet = ? AND scanned_at > ?
        """, (dev_wallet, datetime.now() - timedelta(hours=48)))
        row = await cursor.fetchone()
        if row:
            return {"total_launches": row[0], "successful_launches": row[1],
                   "rugs": row[2], "trust_score": row[3]}
        return None
    
    async def add_performance_tracking(self, address: str, initial_price: float):
        await self.db.execute("""
            INSERT OR IGNORE INTO performance_tracking 
            (address, posted_at, initial_price, last_checked)
            VALUES (?, ?, ?, ?)
        """, (address, datetime.now(), initial_price, datetime.now()))
        await self.db.commit()
        logger.debug(f"Added performance tracking for {address}")
    
    async def get_tracked_performance(self) -> List[dict]:
        cursor = await self.db.execute("""
            SELECT address, initial_price, price_1h, price_6h, price_24h, max_multiple, last_checked
            FROM performance_tracking
        """)
        rows = await cursor.fetchall()
        return [{"address": r[0], "initial_price": r[1], "price_1h": r[2], "price_6h": r[3],
                 "price_24h": r[4], "max_multiple": r[5], "last_checked": datetime.fromisoformat(r[6]) if r[6] else None}
                for r in rows]
    
    async def update_performance_checkpoint(self, address: str, current_price: float, checkpoint: str):
        await self.db.execute(f"""
            UPDATE performance_tracking
            SET price_{checkpoint} = ?, last_checked = ?
            WHERE address = ?
        """, (current_price, datetime.now(), address))
        await self.db.commit()
    
    async def update_max_multiple(self, address: str, current_multiple: float):
        await self.db.execute("""
            UPDATE performance_tracking
            SET max_multiple = GREATEST(max_multiple, ?), last_checked = ?
            WHERE address = ?
        """, (current_multiple, datetime.now(), address))
        await self.db.commit()
    
    async def close(self):
        if self.db:
            await self.db.close()

class ConvictionFilter:
    @staticmethod
    async def safety_check(token: TokenData) -> tuple[bool, str]:
        if not token.twitter and not token.telegram and not token.website:
            return False, "No social links (likely scam)"
        if token.liquidity_usd < MIN_LIQUIDITY:
            return False, f"Low liquidity (${token.liquidity_usd:.0f})"
        if token.launch_time:
            age_minutes = (datetime.now() - token.launch_time).total_seconds() / 60
            if age_minutes < MIN_AGE_MINUTES:
                return False, f"Too new ({age_minutes:.0f}m old - possible rug)"
            age_hours = age_minutes / 60
            if age_hours > MAX_AGE_HOURS:
                return False, f"Too old ({age_hours:.0f}h - not early)"
        return True, "Passed safety checks"
    
    @staticmethod
    async def calculate_conviction(token: TokenData) -> tuple[float, List[str]]:
        score = 0.0
        reasons = []
        
        social_score = 0
        if token.twitter:
            social_score += 10
            reasons.append("✓ Twitter verified")
        if token.telegram:
            social_score += 10
            reasons.append("✓ Telegram community")
        if token.website:
            social_score += 5
            reasons.append("✓ Website")
        score += social_score
        
        liq_score = 0
        if token.liquidity_usd >= MIN_LIQUIDITY * 5:
            liq_score += 25
            reasons.append(f"💰 Massive liquidity (${token.liquidity_usd:,.0f})")
        elif token.liquidity_usd >= MIN_LIQUIDITY * 3:
            liq_score += 20
            reasons.append(f"💰 Strong liquidity (${token.liquidity_usd:,.0f})")
        elif token.liquidity_usd >= MIN_LIQUIDITY * 1.5:
            liq_score += 15
            reasons.append(f"✓ Good liquidity (${token.liquidity_usd:,.0f})")
        elif token.liquidity_usd >= MIN_LIQUIDITY:
            liq_score += 10
            reasons.append(f"✓ Adequate liquidity (${token.liquidity_usd:,.0f})")
        score += liq_score
        
        volume_score = 0
        if token.volume_24h > 0 and token.liquidity_usd > 0:
            vol_liq_ratio = token.volume_24h / token.liquidity_usd
            if vol_liq_ratio > 5:
                volume_score += 25
                reasons.append(f"🚀 EXPLOSIVE volume (${token.volume_24h:,.0f})")
            elif vol_liq_ratio > 3:
                volume_score += 20
                reasons.append(f"🔥 Very high volume (${token.volume_24h:,.0f})")
            elif vol_liq_ratio > 1.5:
                volume_score += 15
                reasons.append(f"✓ Strong volume (${token.volume_24h:,.0f})")
            elif vol_liq_ratio > 0.5:
                volume_score += 10
                reasons.append(f"✓ Healthy volume")
        score += volume_score
        
        price_score = 0
        if token.price_change_24h > 100:
            price_score += 15
            reasons.append(f"📈 +{token.price_change_24h:.0f}% (24h) - MOONING")
        elif token.price_change_24h > 50:
            price_score += 12
            reasons.append(f"📈 +{token.price_change_24h:.0f}% (24h)")
        elif token.price_change_24h > 20:
            price_score += 8
            reasons.append(f"📈 +{token.price_change_24h:.0f}% (24h)")
        elif token.price_change_24h > 0:
            price_score += 5
            reasons.append(f"✓ Positive momentum")
        score += price_score
        
        activity_score = 0
        if token.txns_24h_buys > 0 and token.txns_24h_sells > 0:
            buy_sell_ratio = token.txns_24h_buys / max(token.txns_24h_sells, 1)
            total_txns = token.txns_24h_buys + token.txns_24h_sells
            if buy_sell_ratio > 2 and total_txns > 100:
                activity_score += 10
                reasons.append(f"🔥 Heavy buying pressure")
            elif buy_sell_ratio > 1.5 and total_txns > 50:
                activity_score += 7
                reasons.append(f"✓ More buyers than sellers")
            elif total_txns > 100:
                activity_score += 5
                reasons.append(f"✓ High activity")
        score += activity_score
        
        score = min(score, 100)
        return score, reasons

class TelegramPublisher:
    def __init__(self, bot_token: str, channel_id: str):
        self.bot = Bot(token=bot_token)
        self.channel_id = channel_id
        self.last_post_time = 0
    
    async def publish_signal(self, token: TokenData):
        time_since_last = time.time() - self.last_post_time
        if time_since_last < COOLDOWN_SEC:
            wait_time = COOLDOWN_SEC - time_since_last
            logger.info(f"Cooldown active, waiting {wait_time:.0f}s...")
            await asyncio.sleep(wait_time)
        message = self._format_message(token)
        try:
            sent_message = await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            self.last_post_time = time.time()
            logger.info(f"✓ Posted: {token.symbol} (score: {token.conviction_score:.0f})")
            return sent_message.message_id
        except Exception as e:
            logger.error(f"Failed to post {token.symbol}: {e}")
            return None
    
    def _format_message(self, token: TokenData) -> str:
        conviction_emoji = "🔥🔥🔥" if token.conviction_score >= 80 else "🔥🔥" if token.conviction_score >= 65 else "🔥"
        socials = []
        if token.twitter:
            socials.append(f"<a href='{token.twitter}'>Twitter</a>")
        if token.telegram:
            socials.append(f"<a href='{token.telegram}'>Telegram</a>")
        if token.website:
            socials.append(f"<a href='{token.website}'>Website</a>")
        social_links = " | ".join(socials) if socials else "N/A"
        dexscreener = f"https://dexscreener.com/solana/{token.pair_address or token.address}"
        birdeye = f"https://birdeye.so/token/{token.address}?chain=solana"
        reasons_text = "\n".join([f"  • {r}" for r in token.conviction_reasons[:6]])
        age_text = ""
        if token.launch_time:
            age_hours = (datetime.now() - token.launch_time).total_seconds() / 3600
            if age_hours < 1:
                age_text = f"⚡ <{int(age_hours * 60)}m old (EARLY)"
            else:
                age_text = f"🕐 ~{int(age_hours)}h old"
        return f"""
{conviction_emoji} <b>HIGH CONVICTION SIGNAL</b> {conviction_emoji}

<b>{token.name}</b> (${token.symbol})

<b>CA:</b>
<code>{token.address}</code>

<b>Conviction Score:</b> {token.conviction_score:.0f}/100

<b>Why This Could Smash:</b>
{reasons_text}

<b>Socials:</b> {social_links}

<b>Charts:</b> <a href='{dexscreener}'>DexScreener</a> | <a href='{birdeye}'>Birdeye</a>

<b>DEX:</b> {token.dex.upper()}
<b>Liquidity:</b> ${token.liquidity_usd:,.0f}
<b>24h Vol:</b> ${token.volume_24h:,.0f}
<b>24h Change:</b> {token.price_change_24h:+.1f}%
{age_text}

⚠️ <b>DYOR:</b> Not financial advice. High risk = high reward.
""".strip()

# (Add your existing DexScreenerMonitor, TokenFollowUpMonitor, PerformanceTracker, MLLearningEngine, AIAnalysisEngine, ContractRiskScanner, NarrativeTracker, DevWalletTracker, OutcomeTracker classes here if you have them from earlier versions)

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
        self.dev_tracker = DevWalletTracker(self.db) if ENABLE_DEV_TRACKER else None
        self.outcome_tracker = OutcomeTracker(self.db)
        
        self.running = False
    
    async def start(self):
        logger.info("=" * 60)
        logger.info("SENTINEL SIGNALS - Full Advanced Edition")
        logger.info("=" * 60)
        
        await self.db.connect()
        
        if self.ml_engine:
            await self.ml_engine.initialize()
            logger.info("✓ ML engine ready")
        
        self.followup_monitor = TokenFollowUpMonitor(
            self.db, self.publisher, self.monitor.session
        )
        self.performance_tracker = PerformanceTracker(
            self.db, self.publisher, self.monitor.session
        )
        
        self.running = True
        tasks = [
            asyncio.create_task(self.monitor.start(self.process_token)),
            asyncio.create_task(self.followup_monitor.start()),
            asyncio.create_task(self.performance_tracker.start()),
            asyncio.create_task(healthcheck_server()),
        ]
        
        logger.info("✓ All systems operational")
        logger.info(f"✓ Polling DexScreener every {POLL_INTERVAL}s")
        logger.info(f"✓ Follow-up checks every {FOLLOWUP_CHECK_INTERVAL}s")
        logger.info(f"✓ Performance tracking every {PERFORMANCE_CHECK_INTERVAL}s")
        logger.info(f"✓ Healthcheck: http://0.0.0.0:{HEALTHCHECK_PORT}/health")
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutdown signal received...")
            await self.stop()
    
    async def process_token(self, token: TokenData):
        if await self.db.is_seen(token.address):
            return
        
        logger.info(f"New token: {token.symbol} ({token.address[:8]}...)")
        
        safe, reason = await self.filter_engine.safety_check(token)
        if not safe:
            logger.debug(f"  ✗ Filtered: {reason}")
            await self.db.mark_seen(token, posted=False)
            return
        
        score, reasons = await self.filter_engine.calculate_conviction(token)
        
        if self.ml_engine:
            try:
                ml_score, ml_reason = await self.ml_engine.adjust_conviction_with_ml(token, score)
                if abs(ml_score - score) >= 2:
                    reasons.append(f"🤖 {ml_reason}")
                    score = ml_score
            except Exception as e:
                logger.debug(f"ML error: {e}")
        
        if self.ai_engine:
            try:
                ai_result = await self.ai_engine.analyze_token(token)
                ai_adj = ai_result["confidence_adjustment"]
                if ai_adj != 0:
                    score += ai_adj
                    score = max(0, min(100, score))
                    if ai_adj > 0:
                        reasons.append(f"🧠 AI: {ai_result['analysis'][:80]}")
                    else:
                        reasons.insert(0, f"⚠️ AI: {ai_result['analysis'][:80]}")
            except Exception as e:
                logger.debug(f"AI error: {e}")
        
        if self.contract_scanner:
            try:
                contract_scan = await self.contract_scanner.scan_token(token.address)
                safety_score = contract_scan["safety_score"]
                contract_adj = ((safety_score - 50) / 50) * CONTRACT_SAFETY_WEIGHT
                score += contract_adj
                score = max(0, min(100, score))
                if contract_scan["mint_revoked"] and contract_scan["freeze_revoked"]:
                    reasons.append("🛡️ Contract SAFE: Authorities revoked")
                elif contract_scan["red_flags"]:
                    reasons.insert(0, f"⚠️ {contract_scan['red_flags'][0]}")
            except Exception as e:
                logger.debug(f"Contract scan error: {e}")
        
        if self.narrative_tracker:
            try:
                narrative_boost, narrative_reasons = await self.narrative_tracker.calculate_narrative_boost(token)
                if narrative_boost > 0:
                    score += narrative_boost
                    score = max(0, min(100, score))
                    reasons.extend(narrative_reasons)
            except Exception as e:
                logger.debug(f"Narrative error: {e}")
        
        token.conviction_score = score
        token.conviction_reasons = reasons
        
        logger.info(f"  ✓ Scored {score:.0f}/100")
        
        if score < 60:
            logger.info(f"  ✗ Below threshold (60)")
            await self.db.mark_seen(token, posted=False)
            return
        
        recent_posts = await self.db.get_recent_posts_count(hours=1)
        if recent_posts >= MAX_SIGNALS_PER_HOUR:
            logger.warning(f"  ⏸ Hourly limit reached ({recent_posts}/{MAX_SIGNALS_PER_HOUR})")
            await self.db.mark_seen(token, posted=False)
            return
        
        try:
            sent_message = await self.publisher.publish_signal(token)
            await self.db.mark_seen(token, posted=True)
            
            if sent_message:
                snapshot = TokenSnapshot(
                    address=token.address,
                    symbol=token.symbol,
                    name=token.name,
                    pair_address=token.pair_address,
                    posted_at=datetime.now(),
                    initial_liquidity=token.liquidity_usd,
                    initial_volume_24h=token.volume_24h,
                    initial_price=token.price_usd,
                    conviction_score=token.conviction_score
                )
                await self.db.add_tracked_token(snapshot)
                await self.db.add_performance_tracking(token.address, token.price_usd)
            
            logger.info(f"  🚀 POSTED: {token.symbol} | Score: {score:.0f}/100")
        except Exception as e:
            logger.error(f"  ✗ Publish failed: {e}")
    
    async def stop(self):
        logger.info("Shutting down gracefully...")
        self.running = False
        await self.monitor.stop()
        if self.followup_monitor:
            await self.followup_monitor.stop()
        if self.performance_tracker:
            await self.performance_tracker.stop()
        if self.contract_scanner:
            await self.contract_scanner.close()
        await self.db.close()
        logger.info("✓ Shutdown complete")

async def healthcheck_server():
    async def health(request):
        return web.Response(text="OK - Sentinel Signals Running", status=200)
    async def stats(request):
        return web.json_response({
            "status": "running",
            "service": "sentinel-signals-full",
            "timestamp": datetime.now().isoformat()
        })
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/stats', stats)
    app.router.add_get('/', health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HEALTHCHECK_PORT)
    await site.start()
    logger.info(f"✓ Healthcheck server started on port {HEALTHCHECK_PORT}")

async def main():
    if not all([TELEGRAM_TOKEN, CHANNEL_ID]):
        logger.error("=" * 60)
        logger.error("MISSING REQUIRED ENVIRONMENT VARIABLES")
        logger.error("=" * 60)
        logger.error(f"TELEGRAM_BOT_TOKEN: {'✓' if TELEGRAM_TOKEN else '✗ MISSING'}")
        logger.error(f"TELEGRAM_CHANNEL_ID: {'✓' if CHANNEL_ID else '✗ MISSING'}")
        logger.error("=" * 60)
        sys.exit(1)
    logger.info("Environment variables validated ✓")
    logger.info("Using DexScreener API (no key required)")
    sentinel = SentinelSignals()
    max_restarts = 3
    restart_count = 0
    while restart_count < max_restarts:
        try:
            await sentinel.start()
            break
        except KeyboardInterrupt:
            logger.info("Shutdown requested by user/signal")
            break
        except Exception as e:
            restart_count += 1
            logger.critical(f"Fatal error (attempt {restart_count}/{max_restarts}): {e}", exc_info=True)
            if restart_count < max_restarts:
                logger.info(f"Attempting restart in 10 seconds...")
                await asyncio.sleep(10)
            else:
                logger.critical("Max restart attempts reached. Exiting.")
                sys.exit(1)
    await sentinel.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)
        sys.exit(1)

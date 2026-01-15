"""
Sentinel Signals - Elite Solana Memecoin Signal Bot
Architecture: DexScreener API polling → Multi-tier filters → Telegram publisher → Follow-up monitoring
Author: Built for high-conviction signals (5-15/day win rate optimization)
Features: Real-time alerts on posted tokens (rug warnings, volume spikes, price action)
Railway Deployment: Production ready with healthchecks and graceful shutdown
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

# ============================================================================
# SIGNAL HANDLING FOR RAILWAY GRACEFUL SHUTDOWN
# ============================================================================

def signal_handler(signum, frame):
    """Handle SIGTERM from Railway for graceful shutdown"""
    logger = logging.getLogger("SentinelSignals")
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================================================
# CONFIGURATION & INITIALIZATION
# ============================================================================

load_dotenv()

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# DexScreener API (no key required!)
DEXSCREENER_API = "https://api.dexscreener.com"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 45))

# Filter Thresholds
MIN_DESC_LEN = int(os.getenv("MIN_DESCRIPTION_LENGTH", 30))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY_USD", 8000))
MIN_AGE_MINUTES = int(os.getenv("MIN_TOKEN_AGE_MINUTES", 10))
MAX_AGE_HOURS = int(os.getenv("MAX_TOKEN_AGE_HOURS", 12))

# Operational
MAX_SIGNALS_PER_HOUR = int(os.getenv("MAX_SIGNALS_PER_HOUR", 2))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_BETWEEN_POSTS_SEC", 300))
DB_PATH = os.getenv("DATABASE_PATH", "./data/sentinel.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "./logs/sentinel.log")
HEALTHCHECK_PORT = int(os.getenv("PORT", 8080))

# Follow-up Monitoring
FOLLOWUP_CHECK_INTERVAL = int(os.getenv("FOLLOWUP_CHECK_INTERVAL_SEC", 300))  # 5 min
FOLLOWUP_TRACK_DURATION_HOURS = int(os.getenv("FOLLOWUP_TRACK_DURATION_HOURS", 48))

# Alert thresholds
ALERT_VOLUME_DROP_PCT = float(os.getenv("ALERT_VOLUME_DROP_PERCENT", 50))
ALERT_LIQUIDITY_DROP_PCT = float(os.getenv("ALERT_LIQUIDITY_DROP_PERCENT", 60))
ALERT_HOLDER_DROP_PCT = float(os.getenv("ALERT_HOLDER_DROP_PERCENT", 25))
ALERT_VOLUME_SPIKE_PCT = float(os.getenv("ALERT_VOLUME_SPIKE_PERCENT", 200))
ALERT_PRICE_SPIKE_PCT = float(os.getenv("ALERT_PRICE_SPIKE_PERCENT", 100))
ALERT_PRICE_DROP_PCT = float(os.getenv("ALERT_PRICE_DROP_PERCENT", 50))

# Create directories with error handling for Railway
try:
    db_dir = Path(DB_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"Warning: Failed to create directories: {e}")
    DB_PATH = "/tmp/sentinel.db"
    LOG_FILE = "/tmp/sentinel.log"
    print(f"Using fallback paths: DB={DB_PATH}, LOG={LOG_FILE}")

# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='a')
    ]
)
logger = logging.getLogger("SentinelSignals")

# ============================================================================
# DATA MODELS
# ============================================================================

class AlertType(Enum):
    """Types of follow-up alerts"""
    VOLUME_DROP = "volume_drop"
    LIQUIDITY_DROP = "liquidity_drop"
    HOLDER_DROP = "holder_drop"
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
    price_usd: float = 0.0  # Added for follow-up tracking
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
    """Snapshot of token metrics at time of posting for follow-up tracking"""
    address: str
    symbol: str
    name: str
    pair_address: str
    posted_at: datetime
    initial_liquidity: float
    initial_volume_24h: float
    initial_price: float
    initial_market_cap: float
    initial_holder_count: int = 0
    initial_txns_buys: int = 0
    initial_txns_sells: int = 0
    conviction_score: float = 0.0
    
    # Current values (updated during monitoring)
    current_liquidity: float = 0.0
    current_volume_24h: float = 0.0
    current_price: float = 0.0
    current_market_cap: float = 0.0
    current_holder_count: int = 0
    last_checked: Optional[datetime] = None
    
    # Alert tracking
    alerts_sent: Set[AlertType] = field(default_factory=set)

# ============================================================================
# DATABASE LAYER (WITH FOLLOW-UP TRACKING)
# ============================================================================

class TokenDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None
    
    async def connect(self):
        self.db = await aiosqlite.connect(self.db_path)
        
        # Original tables
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
        
        # Follow-up tracking table
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_tokens (
                address TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                pair_address TEXT,
                posted_at TIMESTAMP,
                conviction_score REAL,
                
                -- Initial snapshot
                initial_liquidity REAL,
                initial_volume_24h REAL,
                initial_price REAL,
                initial_market_cap REAL,
                initial_holder_count INTEGER DEFAULT 0,
                initial_txns_buys INTEGER DEFAULT 0,
                initial_txns_sells INTEGER DEFAULT 0,
                
                -- Current values (updated during checks)
                current_liquidity REAL DEFAULT 0,
                current_volume_24h REAL DEFAULT 0,
                current_price REAL DEFAULT 0,
                current_market_cap REAL DEFAULT 0,
                current_holder_count INTEGER DEFAULT 0,
                last_checked TIMESTAMP,
                
                -- Alert tracking (JSON array of alert types sent)
                alerts_sent TEXT DEFAULT '[]',
                
                FOREIGN KEY (address) REFERENCES seen_tokens(address)
            )
        """)
        
        # Alert history for analytics
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
    
    # Follow-up tracking methods
    async def add_tracked_token(self, snapshot: TokenSnapshot):
        """Add a newly posted token to follow-up tracking"""
        await self.db.execute("""
            INSERT OR REPLACE INTO tracked_tokens (
                address, symbol, name, pair_address, posted_at, conviction_score,
                initial_liquidity, initial_volume_24h, initial_price, initial_market_cap,
                initial_holder_count, initial_txns_buys, initial_txns_sells,
                current_liquidity, current_volume_24h, current_price, current_market_cap,
                current_holder_count, last_checked, alerts_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.address, snapshot.symbol, snapshot.name, snapshot.pair_address,
            snapshot.posted_at, snapshot.conviction_score,
            snapshot.initial_liquidity, snapshot.initial_volume_24h, snapshot.initial_price,
            snapshot.initial_market_cap, snapshot.initial_holder_count,
            snapshot.initial_txns_buys, snapshot.initial_txns_sells,
            snapshot.current_liquidity, snapshot.current_volume_24h, snapshot.current_price,
            snapshot.current_market_cap, snapshot.current_holder_count,
            snapshot.last_checked, json.dumps([])
        ))
        await self.db.commit()
        logger.info(f"✓ Added {snapshot.symbol} to follow-up tracking")
    
    async def get_active_tracked_tokens(self) -> List[TokenSnapshot]:
        """Get all tokens being tracked (within tracking duration)"""
        cutoff = datetime.now() - timedelta(hours=FOLLOWUP_TRACK_DURATION_HOURS)
        cursor = await self.db.execute("""
            SELECT address, symbol, name, pair_address, posted_at, conviction_score,
                   initial_liquidity, initial_volume_24h, initial_price, initial_market_cap,
                   initial_holder_count, initial_txns_buys, initial_txns_sells,
                   current_liquidity, current_volume_24h, current_price, current_market_cap,
                   current_holder_count, last_checked, alerts_sent
            FROM tracked_tokens
            WHERE posted_at > ?
        """, (cutoff,))
        
        rows = await cursor.fetchall()
        snapshots = []
        
        for row in rows:
            alerts_sent = set()
            try:
                alerts_json = json.loads(row[19])
                alerts_sent = {AlertType(a) for a in alerts_json}
            except:
                pass
            
            snapshot = TokenSnapshot(
                address=row[0], symbol=row[1], name=row[2], pair_address=row[3],
                posted_at=datetime.fromisoformat(row[4]), conviction_score=row[5],
                initial_liquidity=row[6], initial_volume_24h=row[7],
                initial_price=row[8], initial_market_cap=row[9],
                initial_holder_count=row[10], initial_txns_buys=row[11],
                initial_txns_sells=row[12], current_liquidity=row[13],
                current_volume_24h=row[14], current_price=row[15],
                current_market_cap=row[16], current_holder_count=row[17],
                last_checked=datetime.fromisoformat(row[18]) if row[18] else None,
                alerts_sent=alerts_sent
            )
            snapshots.append(snapshot)
        
        return snapshots
    
    async def update_tracked_token(self, snapshot: TokenSnapshot):
        """Update current metrics for a tracked token"""
        alerts_json = json.dumps([a.value for a in snapshot.alerts_sent])
        
        await self.db.execute("""
            UPDATE tracked_tokens
            SET current_liquidity = ?, current_volume_24h = ?, current_price = ?,
                current_market_cap = ?, current_holder_count = ?, last_checked = ?,
                alerts_sent = ?
            WHERE address = ?
        """, (
            snapshot.current_liquidity, snapshot.current_volume_24h, snapshot.current_price,
            snapshot.current_market_cap, snapshot.current_holder_count,
            datetime.now(), alerts_json, snapshot.address
        ))
        await self.db.commit()
    
    async def log_alert(self, address: str, alert_type: AlertType, change_pct: float, message: str):
        """Log an alert to history"""
        await self.db.execute("""
            INSERT INTO alert_history (address, alert_type, metric_change_pct, message)
            VALUES (?, ?, ?, ?)
        """, (address, alert_type.value, change_pct, message))
        await self.db.commit()
    
    async def cleanup_old_tracked_tokens(self):
        """Remove tokens older than tracking duration"""
        cutoff = datetime.now() - timedelta(hours=FOLLOWUP_TRACK_DURATION_HOURS)
        result = await self.db.execute("DELETE FROM tracked_tokens WHERE posted_at < ?", (cutoff,))
        await self.db.commit()
        logger.debug("Cleaned up old tracked tokens")
    
    async def close(self):
        if self.db:
            await self.db.close()

# ============================================================================
# FILTER ENGINE
# ============================================================================

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
        
        # Social score (0-25 points)
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
        
        # Liquidity score (0-25 points)
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
        
        # Volume score (0-25 points)
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
        
        # Price action score (0-15 points)
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
        
        # Trading activity score (0-10 points)
        activity_score = 0
        if token.txns_24h_buys > 0 and token.txns_24h_sells > 0:
            buy_sell_ratio = token.txns_24h_buys / max(token.txns_24h_sells, 1)
            total_txns = token.txns_24h_buys + token.txns_24h_sells
            
            if buy_sell_ratio > 2 and total_txns > 100:
                activity_score += 10
                reasons.append(f"🔥 Heavy buying pressure ({token.txns_24h_buys}B/{token.txns_24h_sells}S)")
            elif buy_sell_ratio > 1.5 and total_txns > 50:
                activity_score += 7
                reasons.append(f"✓ More buyers than sellers ({token.txns_24h_buys}B/{token.txns_24h_sells}S)")
            elif total_txns > 100:
                activity_score += 5
                reasons.append(f"✓ High activity ({total_txns} txns)")
        score += activity_score
        
        score = min(score, 100)
        return score, reasons

# ============================================================================
# DEXSCREENER MONITOR
# ============================================================================

class DexScreenerMonitor:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
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
                logger.error(f"Error in DexScreener polling loop: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def _fetch_latest_profiles(self, callback):
        """Fetch latest token profiles from DexScreener"""
        try:
            url = f"{DEXSCREENER_API}/token-profiles/latest/v1"
            
            async with self.session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning(f"DexScreener profiles returned {resp.status}")
                    await self._search_trending_tokens(callback)
                    return
                
                data = await resp.json()
                profiles = data if isinstance(data, list) else []
                
                if not profiles:
                    logger.debug("No token profiles found")
                    return
                
                logger.info(f"Found {len(profiles)} token profiles from DexScreener")
                
                solana_profiles = [p for p in profiles if p.get("chainId") == "solana"]
                
                for profile in solana_profiles[:15]:
                    try:
                        token_addr = profile.get("tokenAddress")
                        if token_addr:
                            token_data = await self._fetch_token_pairs(token_addr, profile)
                            if token_data:
                                await callback(token_data)
                    except Exception as e:
                        logger.debug(f"Error processing profile: {e}")
                
        except asyncio.TimeoutError:
            logger.warning("DexScreener request timeout")
        except Exception as e:
            logger.error(f"Error fetching profiles: {e}")
    
    async def _search_trending_tokens(self, callback):
        """Fallback: Search for trending Solana tokens"""
        try:
            url = f"{DEXSCREENER_API}/latest/dex/search?q=SOL"
            
            async with self.session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning(f"DexScreener search returned {resp.status}")
                    return
                
                data = await resp.json()
                pairs = data.get("pairs", [])
                
                solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                solana_pairs.sort(key=lambda x: float(x.get("volume", {}).get("h24", 0)), reverse=True)
                
                logger.info(f"Found {len(solana_pairs)} Solana pairs via search")
                
                for pair in solana_pairs[:10]:
                    try:
                        token_data = await self._parse_pair(pair)
                        if token_data:
                            await callback(token_data)
                    except Exception as e:
                        logger.debug(f"Error parsing search pair: {e}")
        except Exception as e:
            logger.debug(f"Search fallback error: {e}")
    
    async def _fetch_token_pairs(self, token_address: str, profile: dict) -> Optional[TokenData]:
        """Fetch pair data for a specific token"""
        try:
            url = f"{DEXSCREENER_API}/latest/dex/tokens/{token_address}"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                pairs = data.get("pairs", [])
                
                if not pairs:
                    return None
                
                pairs.sort(key=lambda x: float(x.get("liquidity", {}).get("usd", 0)), reverse=True)
                best_pair = pairs[0]
                
                return await self._parse_pair(best_pair, profile)
                
        except Exception as e:
            logger.debug(f"Error fetching token pairs: {e}")
            return None
    
    async def _parse_pair(self, pair: dict, profile: dict = None) -> Optional[TokenData]:
        try:
            base_token = pair.get("baseToken", {})
            
            address = base_token.get("address")
            if not address:
                return None
            
            pair_created = pair.get("pairCreatedAt")
            launch_time = None
            if pair_created:
                try:
                    launch_time = datetime.fromtimestamp(pair_created / 1000)
                except:
                    pass
            
            price_change = pair.get("priceChange", {})
            txns = pair.get("txns", {})
            h24 = txns.get("h24", {})
            
            twitter = ""
            telegram = ""
            website = ""
            description = ""
            
            if profile:
                description = profile.get("description", "")
                links = profile.get("links", [])
                for link in links:
                    link_type = link.get("type", "").lower()
                    link_url = link.get("url", "")
                    if "twitter" in link_type or "x.com" in link_url:
                        twitter = link_url
                    elif "telegram" in link_type or "t.me" in link_url:
                        telegram = link_url
                    elif "website" in link_type:
                        website = link_url
            
            info = pair.get("info", {})
            if info:
                socials = info.get("socials", [])
                websites = info.get("websites", [])
                
                for social in socials:
                    s_type = social.get("type", "").lower()
                    s_url = social.get("url", "")
                    if "twitter" in s_type and not twitter:
                        twitter = s_url
                    elif "telegram" in s_type and not telegram:
                        telegram = s_url
                
                for site in websites:
                    if not website:
                        website = site.get("url", "")
            
            return TokenData(
                address=address,
                symbol=base_token.get("symbol", ""),
                name=base_token.get("name", ""),
                description=description,
                twitter=twitter,
                telegram=telegram,
                website=website,
                liquidity_usd=float(pair.get("liquidity", {}).get("usd", 0)),
                volume_24h=float(pair.get("volume", {}).get("h24", 0)),
                market_cap=float(pair.get("fdv", 0)),
                price_usd=float(pair.get("priceUsd", 0)),
                price_change_24h=float(price_change.get("h24", 0)),
                price_change_1h=float(price_change.get("h1", 0)),
                txns_24h_buys=int(h24.get("buys", 0)),
                txns_24h_sells=int(h24.get("sells", 0)),
                source="dexscreener",
                launch_time=launch_time,
                dex=pair.get("dexId", ""),
                pair_address=pair.get("pairAddress", "")
            )
            
        except Exception as e:
            logger.debug(f"Error parsing pair data: {e}")
            return None
    
    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()

# ============================================================================
# TOKEN FOLLOW-UP MONITOR
# ============================================================================

class TokenFollowUpMonitor:
    """Monitors posted tokens for significant changes and sends alerts"""
    
    def __init__(self, db: TokenDatabase, publisher: 'TelegramPublisher', session: aiohttp.ClientSession):
        self.db = db
        self.publisher = publisher
        self.session = session
        self.running = False
    
    async def start(self):
        """Start the follow-up monitoring loop"""
        self.running = True
        logger.info(f"🔍 Follow-up monitor started (checking every {FOLLOWUP_CHECK_INTERVAL}s)")
        
        while self.running:

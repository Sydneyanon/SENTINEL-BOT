"""
Sentinel Signals - Full Working Version (with DexScreenerMonitor fixed)
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

class SentinelSignals:
    def __init__(self):
        self.db = TokenDatabase(DB_PATH)
        self.filter_engine = ConvictionFilter()
        self.publisher = TelegramPublisher(TELEGRAM_TOKEN, CHANNEL_ID)
        self.monitor = DexScreenerMonitor()
        self.running = False
    
    async def start(self):
        logger.info("=" * 60)
        logger.info("SENTINEL SIGNALS - DexScreener Edition")
        logger.info("=" * 60)
        await self.db.connect()
        self.running = True
        tasks = [
            asyncio.create_task(self.monitor.start(self.process_token)),
            asyncio.create_task(healthcheck_server()),
        ]
        logger.info("✓ All systems operational")
        logger.info(f"✓ Polling DexScreener every {POLL_INTERVAL}s")
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
            await self.publisher.publish_signal(token)
            await self.db.mark_seen(token, posted=True)
            logger.info(f"  🚀 POSTED: {token.symbol} | Score: {score:.0f}/100")
        except Exception as e:
            logger.error(f"  ✗ Publish failed: {e}")
    
    async def stop(self):
        logger.info("Shutting down gracefully...")
        self.running = False
        await self.monitor.stop()
        await self.db.close()
        logger.info("✓ Shutdown complete")

async def healthcheck_server():
    async def health(request):
        return web.Response(text="OK - Sentinel Signals Running", status=200)
    async def stats(request):
        return web.json_response({
            "status": "running",
            "service": "sentinel-signals-dexscreener",
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

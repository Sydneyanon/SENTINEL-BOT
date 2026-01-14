"""
Sentinel Signals - Elite Solana Memecoin Signal Bot
Architecture: Async WebSocket monitor → Multi-tier filters → Telegram publisher
Author: Built for high-conviction signals (5-15/day win rate optimization)
"""

import os
import sys
import asyncio
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
from dataclasses import dataclass, asdict
from pathlib import Path

import aiohttp
import aiosqlite
import websockets
from aiogram import Bot
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ============================================================================
# CONFIGURATION & INITIALIZATION
# ============================================================================

load_dotenv()

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Birdeye
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
BIRDEYE_WS_URL = "wss://public-api.birdeye.so/socket"
BIRDEYE_API_URL = "https://public-api.birdeye.so"
PUMPFUN_API = os.getenv("PUMPFUN_API_URL", "https://frontend-api.pump.fun")

# WebSocket config
WS_RECONNECT_DELAY = 5
WS_MAX_RECONNECTS = 10
BIRDEYE_MIN_LIQ_FILTER = float(os.getenv("BIRDEYE_MIN_LIQUIDITY_FILTER", 0))
BIRDEYE_SUBSCRIBE_LISTINGS = os.getenv("BIRDEYE_SUBSCRIBE_LISTINGS", "true").lower() == "true"

# Pump.fun config
PUMPFUN_POLL_INTERVAL = int(os.getenv("PUMPFUN_POLL_INTERVAL", 60))
PUMPFUN_MIN_REPLIES = int(os.getenv("PUMPFUN_MIN_REPLIES", 5))

# Filter Thresholds
MIN_DESC_LEN = int(os.getenv("MIN_DESCRIPTION_LENGTH", 50))
MIN_REPLIES = int(os.getenv("MIN_REPLY_COUNT", 10))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY_USD", 5000))
MAX_TOP_HOLDER = float(os.getenv("MAX_TOP_HOLDER_PERCENT", 15))

# Operational
MAX_SIGNALS_PER_HOUR = int(os.getenv("MAX_SIGNALS_PER_HOUR", 3))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_BETWEEN_POSTS_SEC", 180))
DB_PATH = os.getenv("DATABASE_PATH", "./data/sentinel.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
LOG_FILE = os.getenv("LOG_FILE", "./logs/sentinel.log")

# Create directories
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
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
    price_change_5m: float = 0.0
    source: str = ""
    launch_time: Optional[datetime] = None
    reply_count: int = 0
    holder_count: int = 0
    top_holder_percent: float = 0.0
    conviction_score: float = 0.0
    conviction_reasons: List[str] = None
    
    def __post_init__(self):
        if self.conviction_reasons is None:
            self.conviction_reasons = []

# ============================================================================
# DATABASE LAYER
# ============================================================================

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
    
    async def close(self):
        if self.db:
            await self.db.close()

# ============================================================================
# FILTER ENGINE
# ============================================================================

class ConvictionFilter:
    @staticmethod
    async def safety_check(token: TokenData) -> tuple[bool, str]:
        if not token.twitter and not token.telegram:
            return False, "No social links (likely scam)"
        if len(token.description) < MIN_DESC_LEN:
            return False, f"Description too short ({len(token.description)} chars)"
        if token.source != "pump_dot_fun" and token.liquidity_usd < MIN_LIQUIDITY:
            return False, f"Insufficient liquidity for non-pump launch (${token.liquidity_usd:.0f})"
        if token.top_holder_percent > MAX_TOP_HOLDER:
            return False, f"Top holder owns {token.top_holder_percent:.1f}% (insider risk)"
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
        if len(token.description) > 100:
            social_score += 5
            reasons.append("✓ Detailed description")
        score += social_score
        
        activity_score = 0
        if token.reply_count >= MIN_REPLIES * 2:
            activity_score += 15
            reasons.append(f"🔥 {token.reply_count} early replies (viral)")
        elif token.reply_count >= MIN_REPLIES:
            activity_score += 10
            reasons.append(f"✓ {token.reply_count} replies (good traction)")
        if token.holder_count > 100:
            activity_score += 10
            reasons.append(f"✓ {token.holder_count} holders early")
        score += activity_score
        
        liq_score = 0
        if token.liquidity_usd >= MIN_LIQUIDITY * 3:
            liq_score += 20
            reasons.append(f"💰 Strong liquidity (${token.liquidity_usd:,.0f})")
        elif token.liquidity_usd >= MIN_LIQUIDITY:
            liq_score += 10
            reasons.append(f"✓ Adequate liquidity (${token.liquidity_usd:,.0f})")
        score += liq_score
        
        volume_score = 0
        if token.volume_24h > 0 and token.liquidity_usd > 0:
            vol_liq_ratio = token.volume_24h / token.liquidity_usd
            if vol_liq_ratio > 3:
                volume_score += 15
                reasons.append(f"🚀 Explosive volume (${token.volume_24h:,.0f})")
            elif vol_liq_ratio > 1:
                volume_score += 8
                reasons.append(f"✓ Healthy volume")
        if token.price_change_5m > 20:
            volume_score = min(volume_score + 7, 15)
            reasons.append(f"📈 +{token.price_change_5m:.1f}% (5m)")
        score += volume_score
        
        launch_score = 0
        if token.source == "pump_dot_fun":
            launch_score += 10
            reasons.append("✓ Pump.fun graduation (safe)")
        if token.launch_time:
            age_hours = (datetime.now() - token.launch_time).total_seconds() / 3600
            if age_hours < 1:
                launch_score += 5
                reasons.append("⚡ <1hr old (early)")
            elif age_hours < 6:
                launch_score += 3
        score += launch_score
        
        score = min(score, 100)
        return score, reasons

# ============================================================================
# BIRDEYE MONITOR
# ============================================================================

class BirdeyeMonitor:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.session: Optional[aiohttp.ClientSession] = None
        self.reconnect_count = 0
    
    async def start(self, callback):
        self.running = True
        self.session = aiohttp.ClientSession()
        
        while self.running:
            try:
                logger.info("Connecting to Birdeye WebSocket...")
                
                ws_url = f"{BIRDEYE_WS_URL}/solana?x-api-key={self.api_key}"
                
                async with websockets.connect(
                    ws_url,
                    subprotocols=["echo-protocol"],
                    additional_headers={"Origin": "https://birdeye.so"},
                    ping_interval=30,
                    ping_timeout=10
                ) as ws:
                    self.ws = ws
                    logger.info(f"WS connected - subprotocol: {ws.subprotocol or 'none'}")
                    
                    subscribe_msg = {"type": "SUBSCRIBE_NEW_PAIR"}
                    if BIRDEYE_MIN_LIQ_FILTER > 0:
                        subscribe_msg["min_liquidity"] = BIRDEYE_MIN_LIQ_FILTER
                    
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("✓ Subscribed to SUBSCRIBE_NEW_PAIR")
                    
                    if BIRDEYE_SUBSCRIBE_LISTINGS:
                        await ws.send(json.dumps({"type": "SUBSCRIBE_TOKEN_NEW_LISTING"}))
                        logger.info("✓ Subscribed to SUBSCRIBE_TOKEN_NEW_LISTING")
                    
                    await asyncio.sleep(1)
                    logger.info("🟢 Birdeye WebSocket fully connected and listening")
                    
                    async for message in ws:
                        if not self.running:
                            break
                        
                        if message == "ping":
                            logger.debug("Received ping, sending pong")
                            await ws.pong()
                            continue
                        
                        try:
                            data = json.loads(message)
                            await self._handle_event(data, callback)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON from WS: {message[:100]}")
                        except Exception as e:
                            logger.error(f"Error processing WS message: {e}", exc_info=True)
            
            except websockets.exceptions.WebSocketException as e:
                self.reconnect_count += 1
                close_code = self.ws.close_code if self.ws else "unknown"
                close_reason = self.ws.close_reason if self.ws else "unknown"
                logger.error(f"WS error ({self.reconnect_count}/{WS_MAX_RECONNECTS}): {e} | Close: {close_code} - {close_reason}")
                
                if self.reconnect_count >= WS_MAX_RECONNECTS:
                    logger.critical("Max reconnects! Check API key/Birdeye")
                    await asyncio.sleep(60)
                    self.reconnect_count = 0
                else:
                    await asyncio.sleep(WS_RECONNECT_DELAY * self.reconnect_count)
                    
            except Exception as e:
                logger.error(f"Unexpected WS loop error: {e}", exc_info=True)
                await asyncio.sleep(WS_RECONNECT_DELAY)
            else:
                self.reconnect_count = 0
    
    async def _handle_event(self, data: dict, callback):
        event_type = data.get("type")
        
        if event_type not in ["PRICE_UPDATE", "HEARTBEAT"]:
            logger.debug(f"Birdeye event: {event_type} | Data: {str(data)[:200]}")
        
        if event_type == "NEW_PAIR_DATA":
            payload = data.get("data", {})
            base = payload.get("base", {}) or {}
            mint_address = base.get("address") or payload.get("address")
            
            if not mint_address:
                logger.warning(f"NEW_PAIR_DATA missing base.address")
                return
            
            liquidity = float(payload.get("liquidity", 0))
            source = payload.get("source", "unknown")
            
            logger.info(f"🆕 New pair: {mint_address[:8]}... | ${liquidity:,.0f} | {source}")
            
            token_data = await self._fetch_token_details(mint_address)
            if token_data:
                token_data.liquidity_usd = liquidity or token_data.liquidity_usd
                token_data.source = source or token_data.source
                await callback(token_data)
        
        elif event_type == "TOKEN_NEW_LISTING_DATA":
            payload = data.get("data", {})
            base = payload.get("base", {}) or {}
            mint_address = base.get("address") or payload.get("address")
            
            if not mint_address:
                logger.warning(f"TOKEN_NEW_LISTING_DATA missing address")
                return
            
            logger.info(f"🆕 New listing: {mint_address[:8]}... (pre-liquidity)")
            
            token_data = await self._fetch_token_details(mint_address)
            if token_data:
                if token_data.liquidity_usd == 0:
                    logger.debug(f"  ⚠️ No liquidity yet")
                await callback(token_data)
        
        elif event_type == "HEARTBEAT":
            logger.debug("💓 WS heartbeat")
        
        elif event_type == "ERROR":
            logger.error(f"Birdeye WS error: {data}")
        
        else:
            if event_type and event_type not in ["PRICE_UPDATE"]:
                logger.debug(f"Unhandled event: {event_type}")
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(aiohttp.ClientError))
    async def _fetch_token_details(self, address: str) -> Optional[TokenData]:
        try:
            url = f"{BIRDEYE_API_URL}/defi/token_overview"
            params = {"address": address}
            headers = {"X-API-KEY": self.api_key}
            
            async with self.session.get(url, params=params, headers=headers, timeout=10) as resp:
                if resp.status == 429:
                    logger.warning("Birdeye rate limit, backing off...")
                    await asyncio.sleep(5)
                    return None
                
                resp.raise_for_status()
                data = await resp.json()
                
                if not data.get("success"):
                    return None
                
                token_info = data.get("data", {})
                
                return TokenData(
                    address=address,
                    symbol=token_info.get("symbol", ""),
                    name=token_info.get("name", ""),
                    description=token_info.get("description", ""),
                    twitter=token_info.get("twitter", ""),
                    telegram=token_info.get("telegram", ""),
                    website=token_info.get("website", ""),
                    liquidity_usd=float(token_info.get("liquidity", {}).get("usd", 0)),
                    volume_24h=float(token_info.get("volume24h", 0)),
                    market_cap=float(token_info.get("mc", 0)),
                    price_change_5m=float(token_info.get("price_change_5m", 0)),
                    source=token_info.get("source", ""),
                    holder_count=int(token_info.get("holder", 0)),
                    top_holder_percent=float(token_info.get("top10HolderPercent", 0)),
                    launch_time=datetime.fromtimestamp(token_info.get("createdAt", 0)) if token_info.get("createdAt") else None
                )
        except aiohttp.ClientError as e:
            logger.error(f"API error fetching {address}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching {address}: {e}")
            return None
    
    async def stop(self):
        self.running = False
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()

# ============================================================================
# PUMPFUN MONITOR - FIXED with User-Agent + correct await/text slice
# ============================================================================

class PumpFunMonitor:
    def __init__(self, api_url: str):
        self.api_url = api_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
    
    async def start(self, callback, poll_interval: int = None):
        if poll_interval is None:
            poll_interval = PUMPFUN_POLL_INTERVAL
        
        self.session = aiohttp.ClientSession()
        self.running = True
        logger.info(f"Starting pump.fun monitor (polling every {poll_interval}s)")
        
        while self.running:
            try:
                tokens = await self._fetch_recent_graduations()
                for token in tokens:
                    await callback(token)
                
                await asyncio.sleep(poll_interval)
            
            except Exception as e:
                logger.error(f"Pump.fun polling error: {e}")
                await asyncio.sleep(poll_interval * 2)
    
    async def _fetch_recent_graduations(self) -> List[TokenData]:
        try:
            url = f"{self.api_url}/coins"
            params = {
                "offset": 0,
                "limit": 30,
                "sort": "created_timestamp",
                "order": "DESC"
            }
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            async with self.session.get(url, params=params, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    # FIXED: await text first, then slice
                    text = await resp.text()
                    logger.error(f"Pump.fun returned {resp.status}: {text[:200]}")
                    return []
                
                data = await resp.json()
                
                tokens = []
                
                for item in data:
                    is_graduated = item.get("complete", False) or item.get("raydium_pool")
                    if not is_graduated:
                        continue
                    
                    mint = item.get("mint")
                    if not mint:
                        continue
                    
                    reply_count = int(item.get("reply_count", 0))
                    if reply_count < PUMPFUN_MIN_REPLIES:
                        logger.debug(f"Skipping {item.get('symbol')} - only {reply_count} replies")
                        continue
                    
                    token = TokenData(
                        address=mint,
                        symbol=item.get("symbol", ""),
                        name=item.get("name", ""),
                        description=item.get("description", ""),
                        twitter=item.get("twitter", ""),
                        telegram=item.get("telegram", ""),
                        website=item.get("website", ""),
                        market_cap=float(item.get("usd_market_cap", 0)),
                        source="pump_dot_fun",
                        launch_time=datetime.fromtimestamp(
                            item.get("created_timestamp", 0) / 1000
                        ) if item.get("created_timestamp") else None,
                        reply_count=reply_count,
                        liquidity_usd=float(item.get("raydium_pool", {}).get("liquidity", 0)) if item.get("raydium_pool") else 0
                    )
                    
                    logger.info(
                        f"📈 Pump.fun graduation: {token.symbol} | Replies: {token.reply_count}"
                    )
                    
                    tokens.append(token)
                
                logger.debug(f"Found {len(tokens)} graduated tokens from pump.fun")
                return tokens
        
        except aiohttp.ClientError as e:
            logger.error(f"HTTP error fetching pump.fun: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing pump.fun data: {e}", exc_info=True)
            return []
    
    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()

# ============================================================================
# TELEGRAM PUBLISHER
# ============================================================================

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
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            self.last_post_time = time.time()
            logger.info(f"✓ Posted: {token.symbol} (score: {token.conviction_score:.0f})")
        except Exception as e:
            logger.error(f"Failed to post {token.symbol}: {e}")
    
    def _format_message(self, token: TokenData) -> str:
        conviction_emoji = "🔥🔥🔥" if token.conviction_score >= 80 else "🔥🔥" if token.conviction_score >= 65 else "🔥"
        
        socials = []
        if token.twitter: socials.append(f"<a href='{token.twitter}'>Twitter</a>")
        if token.telegram: socials.append(f"<a href='{token.telegram}'>Telegram</a>")
        if token.website: socials.append(f"<a href='{token.website}'>Website</a>")
        social_links = " | ".join(socials) if socials else "N/A"
        
        dexscreener = f"https://dexscreener.com/solana/{token.address}"
        birdeye = f"https://birdeye.so/token/{token.address}?chain=solana"
        
        reasons_text = "\n".join([f"  • {r}" for r in token.conviction_reasons[:5]])
        
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

<b>Launch:</b> {token.source.replace('_', ' ').title()}
<b>Liquidity:</b> ${token.liquidity_usd:,.0f}
<b>24h Vol:</b> ${token.volume_24h:,.0f}

⚠️ <b>DYOR:</b> Not financial advice. High risk = high reward.
""".strip()

# ============================================================================
# CORE ORCHESTRATOR
# ============================================================================

class SentinelSignals:
    def __init__(self):
        self.db = TokenDatabase(DB_PATH)
        self.filter_engine = ConvictionFilter()
        self.publisher = TelegramPublisher(TELEGRAM_TOKEN, CHANNEL_ID)
        self.birdeye_monitor = BirdeyeMonitor(BIRDEYE_API_KEY)
        self.pumpfun_monitor = PumpFunMonitor(PUMPFUN_API)
        self.running = False
    
    async def start(self):
        logger.info("=" * 60)
        logger.info("SENTINEL SIGNALS - Starting up...")
        logger.info("=" * 60)
        
        await self.db.connect()
        
        self.running = True
        tasks = [
            asyncio.create_task(self.birdeye_monitor.start(self.process_token)),
            asyncio.create_task(self.pumpfun_monitor.start(self.process_token)),
        ]
        
        logger.info("✓ Monitors active")
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutdown...")
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
            logger.info(f"  ✗ Below threshold")
            await self.db.mark_seen(token, posted=False)
            return
        
        recent_posts = await self.db.get_recent_posts_count(hours=1)
        if recent_posts >= MAX_SIGNALS_PER_HOUR:
            logger.warning(f"  ⏸ Hourly limit")
            await self.db.mark_seen(token, posted=False)
            return
        
        try:
            await self.publisher.publish_signal(token)
            await self.db.mark_seen(token, posted=True)
            logger.info(f"  🚀 Posted: {token.symbol}")
        except Exception as e:
            logger.error(f"  ✗ Publish failed: {e}")
    
    async def stop(self):
        logger.info("Shutting down...")
        self.running = False
        await self.birdeye_monitor.stop()
        await self.pumpfun_monitor.stop()
        await self.db.close()
        logger.info("✓ Shutdown complete")

# ============================================================================
# ENTRY POINT
# ============================================================================

async def main():
    if not all([TELEGRAM_TOKEN, CHANNEL_ID, BIRDEYE_API_KEY]):
        logger.error("Missing env vars")
        sys.exit(1)
    
    sentinel = SentinelSignals()
    
    try:
        await sentinel.start()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
        sys.exit(0)

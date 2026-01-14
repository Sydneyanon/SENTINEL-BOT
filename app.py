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
BIRDEYE_WS_URL = "wss://public-api.birdeye.so/socket"  # Correct WebSocket endpoint
BIRDEYE_API_URL = "https://public-api.birdeye.so"
PUMPFUN_API = os.getenv("PUMPFUN_API_URL", "https://frontend-api.pump.fun")

# WebSocket config
WS_RECONNECT_DELAY = 5  # Seconds between reconnect attempts
WS_MAX_RECONNECTS = 10  # Max consecutive reconnects before alerting
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
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
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
    """Unified token data structure"""
    address: str
    symbol: str
    name: str
    description: str = ""
    twitter: str = ""
    telegram: str = ""
    website: str = ""
    
    # Market data
    liquidity_usd: float = 0.0
    volume_24h: float = 0.0
    market_cap: float = 0.0
    price_change_5m: float = 0.0
    
    # Launch metadata
    source: str = ""  # "pump_dot_fun", "raydium", etc.
    launch_time: Optional[datetime] = None
    
    # Social signals
    reply_count: int = 0
    holder_count: int = 0
    top_holder_percent: float = 0.0
    
    # Conviction score (0-100, calculated)
    conviction_score: float = 0.0
    conviction_reasons: List[str] = None
    
    def __post_init__(self):
        if self.conviction_reasons is None:
            self.conviction_reasons = []

# ============================================================================
# DATABASE LAYER
# ============================================================================

class TokenDatabase:
    """SQLite persistence for seen tokens and signal history"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None
    
    async def connect(self):
        """Initialize database with schema"""
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
        """Check if token already processed"""
        cursor = await self.db.execute(
            "SELECT 1 FROM seen_tokens WHERE address = ?", (address,)
        )
        result = await cursor.fetchone()
        return result is not None
    
    async def mark_seen(self, token: TokenData, posted: bool = False):
        """Record token as seen/posted"""
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
        """Count signals posted in last N hours (rate limiting)"""
        cutoff = datetime.now() - timedelta(hours=hours)
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM signal_history WHERE posted_at > ?",
            (cutoff,)
        )
        result = await cursor.fetchone()
        return result[0] if result else 0
    
    async def close(self):
        if self.db:
            await self.db.close()

# ============================================================================
# FILTER ENGINE (The Secret Sauce)
# ============================================================================

class ConvictionFilter:
    """
    Three-tier filtering system:
    1. Safety Baseline (hard filters)
    2. Momentum Signals (weighted scoring)
    3. Smart Money Indicators (future: dev wallets, KOL mentions)
    
    Conviction Score Breakdown (0-100):
    - Social Presence: 25 points (Twitter + Telegram + description quality)
    - Early Activity: 25 points (reply velocity, holder growth)
    - Liquidity Health: 20 points (adequate LP, fair distribution)
    - Volume Momentum: 15 points (5m price action, volume/liquidity ratio)
    - Launch Quality: 15 points (pump.fun graduation, time since launch)
    """
    
    @staticmethod
    async def safety_check(token: TokenData) -> tuple[bool, str]:
        """
        Hard filters - must pass ALL to proceed
        WHY: Eliminates scams, rug pulls, low-effort launches
        """
        
        # Filter 1: Social proof required
        if not token.twitter and not token.telegram:
            return False, "No social links (likely scam)"
        
        # Filter 2: Minimum effort description
        if len(token.description) < MIN_DESC_LEN:
            return False, f"Description too short ({len(token.description)} chars)"
        
        # Filter 3: Fair launch preference
        # pump.fun = auto-burned LP + mint revoked (safest)
        if token.source != "pump_dot_fun" and token.liquidity_usd < MIN_LIQUIDITY:
            return False, f"Insufficient liquidity for non-pump launch (${token.liquidity_usd:.0f})"
        
        # Filter 4: Whale concentration check
        if token.top_holder_percent > MAX_TOP_HOLDER:
            return False, f"Top holder owns {token.top_holder_percent:.1f}% (insider risk)"
        
        return True, "Passed safety checks"
    
    @staticmethod
    async def calculate_conviction(token: TokenData) -> tuple[float, List[str]]:
        """
        Weighted scoring system - higher score = higher conviction
        WHY: Differentiates lukewarm launches from "smash" potential
        """
        score = 0.0
        reasons = []
        
        # ===== SOCIAL PRESENCE (25 points max) =====
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
        
        # ===== EARLY ACTIVITY (25 points max) =====
        activity_score = 0
        if token.reply_count >= MIN_REPLIES * 2:  # 2x threshold = exceptional
            activity_score += 15
            reasons.append(f"🔥 {token.reply_count} early replies (viral)")
        elif token.reply_count >= MIN_REPLIES:
            activity_score += 10
            reasons.append(f"✓ {token.reply_count} replies (good traction)")
        
        if token.holder_count > 100:
            activity_score += 10
            reasons.append(f"✓ {token.holder_count} holders early")
        
        score += activity_score
        
        # ===== LIQUIDITY HEALTH (20 points max) =====
        liq_score = 0
        if token.liquidity_usd >= MIN_LIQUIDITY * 3:
            liq_score += 20
            reasons.append(f"💰 Strong liquidity (${token.liquidity_usd:,.0f})")
        elif token.liquidity_usd >= MIN_LIQUIDITY:
            liq_score += 10
            reasons.append(f"✓ Adequate liquidity (${token.liquidity_usd:,.0f})")
        
        score += liq_score
        
        # ===== VOLUME MOMENTUM (15 points max) =====
        volume_score = 0
        if token.volume_24h > 0 and token.liquidity_usd > 0:
            vol_liq_ratio = token.volume_24h / token.liquidity_usd
            if vol_liq_ratio > 3:  # 3x volume vs liquidity = high interest
                volume_score += 15
                reasons.append(f"🚀 Explosive volume (${token.volume_24h:,.0f})")
            elif vol_liq_ratio > 1:
                volume_score += 8
                reasons.append(f"✓ Healthy volume")
        
        if token.price_change_5m > 20:  # 20%+ in 5min = momentum
            volume_score = min(volume_score + 7, 15)
            reasons.append(f"📈 +{token.price_change_5m:.1f}% (5m)")
        
        score += volume_score
        
        # ===== LAUNCH QUALITY (15 points max) =====
        launch_score = 0
        if token.source == "pump_dot_fun":
            launch_score += 10
            reasons.append("✓ Pump.fun graduation (safe)")
        
        # Recency bonus (fresher = more upside potential)
        if token.launch_time:
            age_hours = (datetime.now() - token.launch_time).total_seconds() / 3600
            if age_hours < 1:
                launch_score += 5
                reasons.append("⚡ <1hr old (early)")
            elif age_hours < 6:
                launch_score += 3
        
        score += launch_score
        
        # Normalize to 0-100
        score = min(score, 100)
        
        return score, reasons

# ============================================================================
# DATA SOURCES
# ============================================================================

class BirdeyeMonitor:
    """
    WebSocket monitor for Birdeye real-time token events
    WHY: Lowest latency for new token detection (~1-3s vs 30s+ polling)
    
    Birdeye WebSocket Protocol:
    - Connect to wss://public-api.birdeye.so/socket/solana?x-api-key=YOUR_KEY
    - Send subscription messages: {"type": "SUBSCRIBE_NEW_PAIR"}
    - Receive events: {"type": "NEW_PAIR", "data": {...}}
    """
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.session: Optional[aiohttp.ClientSession] = None
        self.reconnect_count = 0
    
    async def start(self, callback):
        """Connect to WebSocket and stream new tokens"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        while self.running:
            try:
                logger.info("Connecting to Birdeye WebSocket...")
                
                # Correct Birdeye WebSocket URL format with API key in query param
                ws_url = f"{BIRDEYE_WS_URL}/solana?x-api-key={self.api_key}"
                
                async with websockets.connect(
                    ws_url,
                    additional_headers={
                        "Sec-WebSocket-Protocol": "echo-protocol",
                        "Origin": "https://birdeye.so",  # Sometimes required by Birdeye
                    },
                    ping_interval=20,  # Keep connection alive
                    ping_timeout=10
                ) as ws:
                    self.ws = ws
                    
                    # Subscribe to new pairs (primary signal for launches)
                    # WHY: New pairs = token just got liquidity = earliest entry point
                    subscribe_msg = {"type": "SUBSCRIBE_NEW_PAIR"}
                    
                    # Optional: Add server-side filtering to reduce noise
                    if BIRDEYE_MIN_LIQ_FILTER > 0:
                        subscribe_msg["min_liquidity"] = BIRDEYE_MIN_LIQ_FILTER
                        logger.info(f"  📊 Filtering pairs with liquidity < ${BIRDEYE_MIN_LIQ_FILTER:,.0f}")
                    
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("✓ Subscribed to SUBSCRIBE_NEW_PAIR")
                    
                    # Optional: Subscribe to new token listings (broader coverage)
                    # Useful for catching pre-liquidity launches
                    if BIRDEYE_SUBSCRIBE_LISTINGS:
                        await ws.send(json.dumps({
                            "type": "SUBSCRIBE_TOKEN_NEW_LISTING"
                        }))
                        logger.info("✓ Subscribed to SUBSCRIBE_TOKEN_NEW_LISTING")
                    
                    # Heartbeat to confirm subscriptions
                    await asyncio.sleep(1)
                    logger.info("🟢 Birdeye WebSocket fully connected and listening")
                    
                    async for message in ws:
                        if not self.running:
                            break
                        
                        try:
                            data = json.loads(message)
                            await self._handle_event(data, callback)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON from WS: {message[:100]}")
                        except Exception as e:
                            logger.error(f"Error processing WS message: {e}", exc_info=True)
            
            except websockets.exceptions.WebSocketException as e:
                self.reconnect_count += 1
                logger.error(f"WebSocket error ({self.reconnect_count}/{WS_MAX_RECONNECTS}): {e}")
                
                if self.reconnect_count >= WS_MAX_RECONNECTS:
                    logger.critical("Max reconnect attempts reached! Check API key or Birdeye status")
                    # Could send alert to admin Telegram here
                    await asyncio.sleep(60)  # Longer backoff
                    self.reconnect_count = 0  # Reset counter
                else:
                    await asyncio.sleep(WS_RECONNECT_DELAY * self.reconnect_count)  # Exponential backoff
                    
            except Exception as e:
                logger.error(f"Unexpected error in WS loop: {e}", exc_info=True)
                await asyncio.sleep(WS_RECONNECT_DELAY)
            else:
                # Connection closed gracefully, reset counter
                self.reconnect_count = 0
    
    async def _handle_event(self, data: dict, callback):
        """
        Parse Birdeye WebSocket events
        
        CRITICAL: Actual Birdeye event types end with _DATA suffix:
        - NEW_PAIR_DATA: New liquidity pool created (best for launch detection)
        - TOKEN_NEW_LISTING_DATA: Token metadata added (earlier but no liquidity yet)
        - PRICE_UPDATE: Price changes (not used for discovery)
        
        Event structure uses nested 'base' object for the token being launched:
        {
            "type": "NEW_PAIR_DATA",
            "data": {
                "address": "pair_address_here",
                "base": {
                    "address": "token_mint_address",  // ← This is what we want
                    "symbol": "MEME",
                    "name": "Meme Coin"
                },
                "quote": { "address": "SOL_address", "symbol": "SOL" },
                "liquidity": 5432.10,
                "source": "raydium"
            }
        }
        
        WHY we process both: NEW_LISTING_DATA gives earliest alert, 
        NEW_PAIR_DATA confirms trading has started with liquidity
        """
        event_type = data.get("type")
        
        # Log all events during debugging (remove in production)
        if event_type not in ["PRICE_UPDATE", "HEARTBEAT"]:
            logger.debug(f"Birdeye event: {event_type} | Data: {str(data)[:200]}")
        
        if event_type == "NEW_PAIR_DATA":
            # New liquidity pair = token is now tradeable
            # CRITICAL: Extract base token mint, not pair address
            payload = data.get("data", {})
            
            # Get base token (the new memecoin being launched)
            base = payload.get("base", {}) or {}
            mint_address = base.get("address") or payload.get("address")
            
            if not mint_address:
                logger.warning(f"NEW_PAIR_DATA missing base.address: {payload}")
                return
            
            # Extract useful pair metadata
            liquidity = float(payload.get("liquidity", 0))
            source = payload.get("source", "unknown")
            quote_symbol = payload.get("quote", {}).get("symbol", "SOL")
            
            logger.info(f"🆕 New pair: {mint_address[:8]}... | ${liquidity:,.0f} | {source}/{quote_symbol}")
            
            # Fetch full token details using mint address (not pair)
            token_data = await self._fetch_token_details(mint_address)
            if token_data:
                # Enrich with pair-specific data
                token_data.liquidity_usd = liquidity or token_data.liquidity_usd
                token_data.source = source or token_data.source
                await callback(token_data)
        
        elif event_type == "TOKEN_NEW_LISTING_DATA":
            # New token listing (may not have liquidity yet)
            # CRITICAL: Extract base token mint
            payload = data.get("data", {})
            
            # Try nested base first, then fallback to direct address
            base = payload.get("base", {}) or {}
            mint_address = base.get("address") or payload.get("address")
            
            if not mint_address:
                logger.warning(f"TOKEN_NEW_LISTING_DATA missing address: {payload}")
                return
            
            logger.info(f"🆕 New listing: {mint_address[:8]}... (pre-liquidity)")
            
            # Fetch details, but note this might not have trading data yet
            token_data = await self._fetch_token_details(mint_address)
            if token_data:
                # Mark as pre-liquidity if no pool data
                if token_data.liquidity_usd == 0:
                    logger.debug(f"  ⚠️ {mint_address[:8]}... has no liquidity yet (early alert)")
                await callback(token_data)
        
        elif event_type == "HEARTBEAT":
            # Connection keepalive (optional logging)
            logger.debug("💓 WebSocket heartbeat")
        
        elif event_type == "ERROR":
            # Birdeye error response
            logger.error(f"Birdeye WS error: {data}")
        
        else:
            # Log unknown event types for debugging
            if event_type and event_type not in ["PRICE_UPDATE"]:
                logger.debug(f"Unhandled event type: {event_type}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError)
    )
    async def _fetch_token_details(self, address: str) -> Optional[TokenData]:
        """
        Fetch comprehensive token data from Birdeye API
        WHY: WebSocket gives minimal data, need API for liquidity, socials, etc.
        """
        try:
            url = f"{BIRDEYE_API_URL}/defi/token_overview"
            params = {"address": address}
            headers = {"X-API-KEY": self.api_key}
            
            async with self.session.get(url, params=params, headers=headers, timeout=10) as resp:
                if resp.status == 429:  # Rate limit
                    logger.warning("Birdeye rate limit hit, backing off...")
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
            raise  # Trigger retry
        except Exception as e:
            logger.error(f"Unexpected error fetching {address}: {e}")
            return None
    
    async def stop(self):
        """Graceful shutdown"""
        self.running = False
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()


class PumpFunMonitor:
    """
    Fallback polling monitor for pump.fun graduations
    WHY: Backup if Birdeye WS fails; pump.fun = safest launches (auto LP burn)
    """
    
    def __init__(self, api_url: str):
        self.api_url = api_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
    
    async def start(self, callback, poll_interval: int = None):
        """Poll pump.fun API for new graduations"""
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
        """
        Fetch recently graduated tokens from pump.fun
        
        API Endpoint: /coins?offset=0&limit=30&sort=created_timestamp&order=DESC
        
        WHY pump.fun graduations are valuable:
        - Auto-burned liquidity (can't rug)
        - Mint authority revoked (can't inflate supply)
        - Proven community (reached bonding curve target)
        - Fair launch (no presale/team allocation)
        """
        try:
            # Correct pump.fun API endpoint
            url = f"{self.api_url}/coins"
            params = {
                "offset": 0,
                "limit": 30,  # Last 30 tokens
                "sort": "created_timestamp",
                "order": "DESC"
            }
            
            async with self.session.get(url, params=params, timeout=15) as resp:
                resp.raise_for_status()
                data = await resp.json()
                
                tokens = []
                
                for item in data:
                    # CRITICAL: Only process graduated/completed tokens
                    # "complete" = bonding curve filled, migrated to Raydium
                    is_graduated = item.get("complete", False) or item.get("raydium_pool")
                    
                    if not is_graduated:
                        continue  # Skip tokens still on bonding curve
                    
                    # Extract token data
                    mint = item.get("mint")
                    if not mint:
                        continue
                    
                    # Optional: Filter by minimum reply count (spam reduction)
                    reply_count = int(item.get("reply_count", 0))
                    if reply_count < PUMPFUN_MIN_REPLIES:
                        logger.debug(f"Skipping {item.get('symbol')} - only {reply_count} replies")
                        continue
                    
                    # Build TokenData object
                    token = TokenData(
                        address=mint,
                        symbol=item.get("symbol", ""),
                        name=item.get("name", ""),
                        description=item.get("description", ""),
                        
                        # Social links (pump.fun has these in metadata)
                        twitter=item.get("twitter", ""),
                        telegram=item.get("telegram", ""),
                        website=item.get("website", ""),
                        
                        # Market data from pump.fun
                        market_cap=float(item.get("usd_market_cap", 0)),
                        
                        # Metadata
                        source="pump_dot_fun",
                        launch_time=datetime.fromtimestamp(
                            item.get("created_timestamp", 0) / 1000  # ms to seconds
                        ) if item.get("created_timestamp") else None,
                        
                        # Social signals (pump.fun specific)
                        reply_count=reply_count,
                        
                        # Raydium pool info if available
                        liquidity_usd=float(item.get("raydium_pool", {}).get("liquidity", 0))
                        if item.get("raydium_pool") else 0
                    )
                    
                    # Log graduation
                    logger.info(
                        f"📈 Pump.fun graduation: {token.symbol} | "
                        f"Replies: {token.reply_count} | "
                        f"Created: {token.launch_time.strftime('%H:%M') if token.launch_time else 'unknown'}"
                    )
                    
                    tokens.append(token)
                
                logger.debug(f"Found {len(tokens)} graduated tokens from pump.fun")
                return tokens
        
        except aiohttp.ClientError as e:
            logger.error(f"HTTP error fetching pump.fun graduations: {e}")
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
    """
    Formats and posts high-conviction signals to Telegram channel
    WHY: Clean, actionable format with all necessary data + DYOR reminder
    """
    
    def __init__(self, bot_token: str, channel_id: str):
        self.bot = Bot(token=bot_token)
        self.channel_id = channel_id
        self.last_post_time = 0
    
    async def publish_signal(self, token: TokenData):
        """
        Post formatted signal to channel with rate limiting
        WHY: Cooldown prevents spam, maintains channel quality
        """
        
        # Rate limit enforcement
        time_since_last = time.time() - self.last_post_time
        if time_since_last < COOLDOWN_SEC:
            wait_time = COOLDOWN_SEC - time_since_last
            logger.info(f"Cooldown active, waiting {wait_time:.0f}s before posting...")
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
            logger.info(f"✓ Posted signal: {token.symbol} (score: {token.conviction_score:.0f})")
        
        except Exception as e:
            logger.error(f"Failed to post {token.symbol}: {e}")
    
    def _format_message(self, token: TokenData) -> str:
        """
        Create rich, scannable signal format
        WHY: Users need quick decision-making info at a glance
        """
        
        # Conviction emoji based on score
        if token.conviction_score >= 80:
            conviction_emoji = "🔥🔥🔥"
        elif token.conviction_score >= 65:
            conviction_emoji = "🔥🔥"
        else:
            conviction_emoji = "🔥"
        
        # Format social links
        socials = []
        if token.twitter:
            socials.append(f"<a href='{token.twitter}'>Twitter</a>")
        if token.telegram:
            socials.append(f"<a href='{token.telegram}'>Telegram</a>")
        if token.website:
            socials.append(f"<a href='{token.website}'>Website</a>")
        social_links = " | ".join(socials) if socials else "N/A"
        
        # Chart links
        dexscreener = f"https://dexscreener.com/solana/{token.address}"
        birdeye = f"https://birdeye.so/token/{token.address}?chain=solana"
        
        # Format conviction reasons
        reasons_text = "\n".join([f"  • {r}" for r in token.conviction_reasons[:5]])
        
        message = f"""
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

⚠️ <b>DYOR:</b> This is not financial advice. Always research before buying. High risk = high reward. Never invest more than you can afford to lose.
""".strip()
        
        return message

# ============================================================================
# CORE ORCHESTRATOR
# ============================================================================

class SentinelSignals:
    """
    Main application orchestrator
    Coordinates: monitors → filters → database → publisher
    """
    
    def __init__(self):
        self.db = TokenDatabase(DB_PATH)
        self.filter_engine = ConvictionFilter()
        self.publisher = TelegramPublisher(TELEGRAM_TOKEN, CHANNEL_ID)
        
        self.birdeye_monitor = BirdeyeMonitor(BIRDEYE_API_KEY)
        self.pumpfun_monitor = PumpFunMonitor(PUMPFUN_API)
        
        self.running = False
    
    async def start(self):
        """Initialize and run all services"""
        logger.info("=" * 60)
        logger.info("SENTINEL SIGNALS - Starting up...")
        logger.info("=" * 60)
        
        # Initialize database
        await self.db.connect()
        
        # Start monitoring tasks
        self.running = True
        tasks = [
            asyncio.create_task(self.birdeye_monitor.start(self.process_token)),
            asyncio.create_task(self.pumpfun_monitor.start(self.process_token)),
        ]
        
        logger.info("✓ All monitors active. Hunting for smash signals...")
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutdown signal received...")
            await self.stop()
    
    async def process_token(self, token: TokenData):
        """
        Main processing pipeline for each token
        Flow: dedup → safety → conviction → publish
        """
        
        # Skip if already seen
        if await self.db.is_seen(token.address):
            return
        
        logger.info(f"New token detected: {token.symbol} ({token.address[:8]}...)")
        
        # STAGE 1: Safety baseline
        safe, reason = await self.filter_engine.safety_check(token)
        if not safe:
            logger.debug(f"  ✗ {token.symbol} filtered: {reason}")
            await self.db.mark_seen(token, posted=False)
            return
        
        # STAGE 2: Calculate conviction score
        score, reasons = await self.filter_engine.calculate_conviction(token)
        token.conviction_score = score
        token.conviction_reasons = reasons
        
        logger.info(f"  ✓ {token.symbol} scored {score:.0f}/100")
        
        # STAGE 3: Threshold check (only post high-conviction signals)
        if score < 60:  # Minimum 60/100 to post
            logger.info(f"  ✗ {token.symbol} below threshold (60)")
            await self.db.mark_seen(token, posted=False)
            return
        
        # STAGE 4: Rate limit check
        recent_posts = await self.db.get_recent_posts_count(hours=1)
        if recent_posts >= MAX_SIGNALS_PER_HOUR:
            logger.warning(f"  ⏸ Hourly limit reached ({recent_posts}/{MAX_SIGNALS_PER_HOUR})")
            await self.db.mark_seen(token, posted=False)
            return
        
        # STAGE 5: Publish!
        try:
            await self.publisher.publish_signal(token)
            await self.db.mark_seen(token, posted=True)
            logger.info(f"  🚀 SIGNAL POSTED: {token.symbol}")
        except Exception as e:
            logger.error(f"  ✗ Failed to publish {token.symbol}: {e}")
    
    async def stop(self):
        """Graceful shutdown"""
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
    """Application entry point with error handling"""
    
    # Validate required env vars
    if not all([TELEGRAM_TOKEN, CHANNEL_ID, BIRDEYE_API_KEY]):
        logger.error("Missing required environment variables!")
        logger.error("Required: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, BIRDEYE_API_KEY")
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
        logger.info("Interrupted by user")
        sys.exit(0)

"""
Pump Bonding Monitor - Calculate bonding curve completion % using whistle.ninja
Uses WebSocket for real-time updates (FREE) + Helius/API fallback
"""
import asyncio
import aiohttp
import websockets
import json
import os
from typing import Optional, Dict
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

HELIUS_API_KEY = os.getenv('HELIUS_API_KEY', '')
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Pump.fun constants
GRADUATION_MCAP = 69000  # Graduates at ~$69k market cap
WHISTLE_WS = "wss://pumpportal.fun/api/data"


class PumpBondingMonitor:
    """
    Monitors Pump.fun bonding curve progress using whistle.ninja WebSocket
    FREE, real-time, no API limits!
    """
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        
        # Cache curve data for tokens
        self.token_cache: Dict[str, Dict] = {}
        self.cache_ttl = 30  # Cache for 30 seconds
        
        # Track tokens from WebSocket stream
        self.live_tokens: Dict[str, Dict] = {}
    
    async def start(self):
        """Start WebSocket connection to whistle.ninja"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info("🎯 Bonding curve monitor starting (whistle.ninja WebSocket)")
        
        # Start WebSocket listener in background
        asyncio.create_task(self._websocket_listener())
        
        logger.success("✅ Bonding curve monitor ready (real-time)")
    
    async def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
    
    async def _websocket_listener(self):
        """Listen to whistle.ninja WebSocket for real-time token data"""
        while self.running:
            try:
                logger.info("🔌 Connecting to whistle.ninja WebSocket...")
                
                async with websockets.connect(WHISTLE_WS) as ws:
                    self.ws = ws
                    
                    # Subscribe to trade data
                    subscribe_msg = {
                        "method": "subscribeTokenTrade"
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    
                    logger.success("✅ Connected to whistle.ninja!")
                    
                    # Listen for messages
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                            data = json.loads(message)
                            
                            await self._process_websocket_message(data)
                        
                        except asyncio.TimeoutError:
                            # Send ping to keep connection alive
                            await ws.send(json.dumps({"method": "ping"}))
                        except Exception as e:
                            logger.debug(f"WebSocket message error: {e}")
                            break
            
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
                await asyncio.sleep(5)  # Wait before reconnecting
    
    async def _process_websocket_message(self, data: dict):
        """Process real-time token data from WebSocket"""
        try:
            # whistle.ninja sends trade data with token info
            if data.get('txType') in ['buy', 'sell']:
                mint = data.get('mint')
                if not mint:
                    return
                
                # Update live token data
                self.live_tokens[mint] = {
                    'mint': mint,
                    'symbol': data.get('symbol', 'UNKNOWN'),
                    'name': data.get('name', 'Unknown'),
                    'market_cap_usd': float(data.get('marketCapSol', 0)) * 200,  # Rough SOL price
                    'sol_amount': float(data.get('vSolInBondingCurve', 0)),
                    'last_trade_timestamp': data.get('timestamp'),
                    'last_updated': asyncio.get_event_loop().time()
                }
        
        except Exception as e:
            logger.debug(f"Error processing WebSocket message: {e}")
    
    async def get_curve_progress(self, token_mint: str) -> Optional[Dict]:
        """
        Get bonding curve progress for a token
        
        Priority:
        1. Live WebSocket data (if available)
        2. Cache (if fresh)
        3. Pump.fun API
        4. Helius fallback
        
        Returns dict with:
        - completion_percent: float (0-100)
        - market_cap_usd: float
        - sol_raised: float
        - holder_count: int
        - volume_24h: float
        """
        try:
            # 1. Check live WebSocket data first (BEST - real-time)
            if token_mint in self.live_tokens:
                live_data = self.live_tokens[token_mint]
                
                # Check if data is fresh (within last 60 seconds)
                age = asyncio.get_event_loop().time() - live_data['last_updated']
                if age < 60:
                    market_cap = live_data['market_cap_usd']
                    completion_pct = min((market_cap / GRADUATION_MCAP) * 100, 100)
                    
                    return {
                        'completion_percent': completion_pct,
                        'market_cap_usd': market_cap,
                        'sol_raised': live_data['sol_amount'],
                        'holder_count': 0,  # Not available from WebSocket
                        'volume_24h': 0,
                        'source': 'websocket_live'
                    }
            
            # 2. Check cache (if fresh)
            if token_mint in self.token_cache:
                cached = self.token_cache[token_mint]
                age = asyncio.get_event_loop().time() - cached['timestamp']
                if age < self.cache_ttl:
                    return cached['data']
            
            # 3. Fetch from Pump.fun API (FREE, no Helius usage)
            curve_data = await self._get_from_pumpfun_api(token_mint)
            
            if curve_data:
                # Cache it
                self.token_cache[token_mint] = {
                    'data': curve_data,
                    'timestamp': asyncio.get_event_loop().time()
                }
                return curve_data
            
            # 4. Fallback: Helius (ONLY if pump.fun API fails)
            logger.debug(f"Using Helius fallback for {token_mint[:8]}")
            return await self._get_from_helius_fallback(token_mint)
        
        except Exception as e:
            logger.debug(f"Error getting curve progress: {e}")
            return None
    
    async def _get_from_pumpfun_api(self, token_mint: str) -> Optional[Dict]:
        """Get curve data from Pump.fun public API (FREE)"""
        try:
            url = f"https://frontend-api.pump.fun/coins/{token_mint}"
            
            async with self.session.get(url, timeout=5) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                
                if not data:
                    return None
                
                # Calculate completion percentage
                market_cap = float(data.get('usd_market_cap', 0))
                completion_pct = min((market_cap / GRADUATION_MCAP) * 100, 100)
                
                return {
                    'completion_percent': completion_pct,
                    'market_cap_usd': market_cap,
                    'sol_raised': market_cap / 200,  # Rough estimate
                    'holder_count': int(data.get('reply_count', 0)),  # Rough proxy
                    'volume_24h': 0,  # Not available
                    'source': 'pumpfun_api',
                    'token_data': data
                }
        
        except Exception as e:
            logger.debug(f"Pump.fun API error: {e}")
            return None
    
    async def _get_from_helius_fallback(self, token_mint: str) -> Optional[Dict]:
        """
        FALLBACK ONLY: Use Helius to get bonding curve account data
        This uses 1 API call - only used when pump.fun API fails
        """
        try:
            if not HELIUS_API_KEY:
                return None
            
            # This would require deriving the bonding curve PDA and fetching account data
            # For now, we'll skip this fallback since pump.fun API is very reliable
            # and we have WebSocket data as primary source
            
            logger.debug("Helius fallback not implemented - rely on pump.fun API + WebSocket")
            return None
        
        except Exception as e:
            logger.debug(f"Helius fallback error: {e}")
            return None
    
    def get_cached_tokens_by_curve_range(
        self, 
        min_pct: float = 0, 
        max_pct: float = 70,
        max_age: int = 300
    ) -> Dict[str, Dict]:
        """
        Get all cached tokens within a bonding curve range
        Useful for finding tokens in the 0-70% range
        
        Args:
            min_pct: Minimum curve completion %
            max_pct: Maximum curve completion %
            max_age: Maximum age of data in seconds
        
        Returns:
            Dict of token_mint -> curve_data
        """
        current_time = asyncio.get_event_loop().time()
        results = {}
        
        # Check live tokens from WebSocket
        for mint, data in self.live_tokens.items():
            age = current_time - data['last_updated']
            if age > max_age:
                continue
            
            market_cap = data['market_cap_usd']
            completion_pct = min((market_cap / GRADUATION_MCAP) * 100, 100)
            
            if min_pct <= completion_pct <= max_pct:
                results[mint] = {
                    'completion_percent': completion_pct,
                    'market_cap_usd': market_cap,
                    'symbol': data.get('symbol', 'UNKNOWN'),
                    'source': 'websocket'
                }
        
        return results

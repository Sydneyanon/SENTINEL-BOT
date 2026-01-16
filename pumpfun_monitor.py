"""
Pump.fun Monitor via whistle.ninja WebSocket - Real-time push notifications
"""

import asyncio
import json
import websockets
import aiohttp
from typing import Optional, Callable
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

WHISTLE_WS = "wss://pump.whistle.ninja/ws"


class PumpfunMonitor:
    """Monitors pump.fun via whistle.ninja WebSocket for real-time graduations"""
    
    def __init__(self):
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.seen_tokens = set()
        self.callback = None
    
    async def start(self, callback: Callable):
        """Start monitoring pump.fun via whistle.ninja WebSocket"""
        self.running = True
        self.callback = callback
        self.http_session = aiohttp.ClientSession()
        
        logger.info("🟢 Connecting to whistle.ninja WebSocket...")
        logger.info("🎯 Real-time push notifications - no polling needed!")
        
        # Keep reconnecting if disconnected
        while self.running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"WebSocket error: {e}", exc_info=True)
                logger.info("🔄 Reconnecting in 10 seconds...")
                await asyncio.sleep(10)
    
    async def stop(self):
        """Stop the monitor"""
        self.running = False
        if self.ws:
            await self.ws.close()
        if self.http_session:
            await self.http_session.close()
    
    async def _connect_and_listen(self):
        """Connect to WebSocket and listen for messages"""
        async with websockets.connect(WHISTLE_WS) as ws:
            self.ws = ws
            logger.info("✅ Connected to whistle.ninja WebSocket!")
            
            # Subscribe to new token events
            subscribe_msg = {
                "type": "subscribe",
                "channel": "pumpfun:new"
            }
            await ws.send(json.dumps(subscribe_msg))
            logger.info("📡 Subscribed to pumpfun:new channel")
            
            # Start ping/pong task to keep connection alive
            ping_task = asyncio.create_task(self._keepalive(ws))
            
            try:
                # Listen for messages
                async for message in ws:
                    try:
                        data = json.loads(message)
                        await self._handle_message(data)
                    except json.JSONDecodeError:
                        logger.debug(f"Invalid JSON: {message}")
                    except Exception as e:
                        logger.error(f"Error handling message: {e}", exc_info=True)
            finally:
                ping_task.cancel()
    
    async def _keepalive(self, ws):
        """Send ping every 30 seconds to keep connection alive"""
        while True:
            try:
                await asyncio.sleep(30)
                await ws.send(json.dumps({"type": "ping"}))
                logger.debug("📡 Sent keepalive ping")
            except Exception as e:
                logger.debug(f"Keepalive error: {e}")
                break
    
    async def _handle_message(self, data: dict):
        """Handle incoming WebSocket message"""
        msg_type = data.get('type')
        channel = data.get('channel')
        
        # Handle pong response
        if msg_type == 'pong':
            logger.debug("✓ Received pong")
            return
        
        # Handle new token events
        if channel == 'pumpfun:new':
            token_data = data.get('data', {})
            
            # Check if it's a graduation
            is_graduated = token_data.get('complete') or token_data.get('raydium_pool')
            
            if not is_graduated:
                logger.debug(f"New token but not graduated: {token_data.get('symbol', 'UNKNOWN')}")
                return
            
            mint = token_data.get('mint')
            if not mint or mint in self.seen_tokens:
                return
            
            self.seen_tokens.add(mint)
            
            # Limit seen set size
            if len(self.seen_tokens) > 1000:
                self.seen_tokens = set(list(self.seen_tokens)[-500:])
            
            symbol = token_data.get('symbol', 'UNKNOWN')
            name = token_data.get('name', 'Unknown')
            
            logger.info(f"🎓 GRADUATION: {symbol} ({name})")
            logger.info(f"   Mint: {mint[:8]}...")
            
            # Fetch full data from DexScreener
            full_data = await self._fetch_dex_data(mint, token_data)
            
            if full_data:
                await self.callback(full_data)
            else:
                logger.warning(f"Could not fetch DEX data for {symbol}")
    
    async def _fetch_dex_data(self, mint: str, pump_data: dict) -> Optional[dict]:
        """Fetch token data from DexScreener"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            
            async with self.http_session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.debug(f"DexScreener returned {resp.status} for {mint}")
                    return None
                
                data = await resp.json()
                pairs = data.get('pairs', [])
                
                if not pairs:
                    logger.debug(f"No pairs found for {mint}")
                    return None
                
                # Get Raydium pair (graduated tokens)
                raydium_pairs = [p for p in pairs if 'raydium' in p.get('dexId', '').lower()]
                
                if not raydium_pairs:
                    logger.debug(f"No Raydium pair for {mint}")
                    return None
                
                pair = raydium_pairs[0]
                
                token_data = {
                    'address': mint,
                    'symbol': pair.get('baseToken', {}).get('symbol', pump_data.get('symbol', 'UNKNOWN')),
                    'name': pair.get('baseToken', {}).get('name', pump_data.get('name', 'Unknown')),
                    'priceUsd': float(pair.get('priceUsd', 0)),
                    'liquidity_usd': float(pair.get('liquidity', {}).get('usd', 0)),
                    'volume_24h': float(pair.get('volume', {}).get('h24', 0)),
                    'price_change_24h': float(pair.get('priceChange', {}).get('h24', 0)),
                    'txns_24h_buys': int(pair.get('txns', {}).get('h24', {}).get('buys', 0)),
                    'txns_24h_sells': int(pair.get('txns', {}).get('h24', {}).get('sells', 0)),
                    'pair_address': pair.get('pairAddress', ''),
                    'dex_id': pair.get('dexId', ''),
                    'url': pair.get('url', ''),
                    'graduation_source': 'pump.fun',
                }
                
                logger.info(f"✅ Got DEX data: ${token_data['liquidity_usd']:,.0f} liq, ${token_data['volume_24h']:,.0f} vol")
                return token_data
        
        except asyncio.TimeoutError:
            logger.debug(f"DexScreener timeout for {mint}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching DEX data: {e}")
            return None

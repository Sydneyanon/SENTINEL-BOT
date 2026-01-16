"""
Graduating Monitor - Catches tokens at 85-95% bonding curve (early entry)
"""

import asyncio
import aiohttp
import os
from typing import Optional, Callable
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

WHISTLE_API = os.getenv('WHISTLE_API', 'https://pump.whistle.ninja')
POLL_INTERVAL = int(os.getenv('GRADUATING_POLL_INTERVAL', 90))  # Check every 90s
MIN_BONDING_CURVE = float(os.getenv('MIN_BONDING_CURVE', 85.0))  # 85%+


class GraduatingMonitor:
    """Monitors tokens near graduation (85-95% bonding curve) for early signals"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.seen_tokens = set()
    
    async def start(self, callback: Callable):
        """Start monitoring graduating tokens"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"🔥 Graduating monitor started (85%+ bonding curve)")
        logger.info(f"⚡ Polling every {POLL_INTERVAL}s for early entry signals")
        
        while self.running:
            try:
                await self._fetch_graduating(callback)
                await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error(f"Graduating monitor error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def stop(self):
        """Stop the monitor"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _fetch_graduating(self, callback: Callable):
        """Fetch tokens near graduation"""
        try:
            url = f"{WHISTLE_API}/api/pumpfun/graduating"
            
            async with self.session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.debug(f"Graduating endpoint returned {resp.status}")
                    return
                
                data = await resp.json()
                tokens = data if isinstance(data, list) else data.get('tokens', data.get('data', []))
                
                if not tokens:
                    logger.debug("No graduating tokens found")
                    return
                
                # Filter by bonding curve percentage
                near_graduation = []
                for token in tokens:
                    curve = token.get('bonding_curve_progress', 0)
                    
                    # Only tokens at 85-95% (catching them BEFORE graduation)
                    if MIN_BONDING_CURVE <= curve < 100:
                        near_graduation.append(token)
                
                if near_graduation:
                    logger.info(f"⚡ Found {len(near_graduation)} tokens near graduation")
                
                # Process each token
                for token in near_graduation:
                    try:
                        mint = token.get('mint')
                        
                        if not mint or mint in self.seen_tokens:
                            continue
                        
                        self.seen_tokens.add(mint)
                        
                        # Limit seen set
                        if len(self.seen_tokens) > 1000:
                            self.seen_tokens = set(list(self.seen_tokens)[-500:])
                        
                        symbol = token.get('symbol', 'UNKNOWN')
                        curve = token.get('bonding_curve_progress', 0)
                        
                        logger.info(f"🔥 GRADUATING SOON: {symbol} ({curve:.1f}% bonding curve)")
                        
                        # Build token data
                        token_data = await self._build_token_data(mint, token)
                        
                        if token_data:
                            # Mark as early signal
                            token_data['signal_type'] = 'graduating'
                            token_data['bonding_curve_progress'] = curve
                            await callback(token_data)
                    
                    except Exception as e:
                        logger.debug(f"Error processing token: {e}")
        
        except asyncio.TimeoutError:
            logger.warning("Graduating endpoint timeout")
        except Exception as e:
            logger.error(f"Error fetching graduating tokens: {e}", exc_info=True)
    
    async def _build_token_data(self, mint: str, pump_data: dict) -> Optional[dict]:
        """Build token data from pump.fun info"""
        try:
            # For graduating tokens, we don't have DexScreener data yet
            # Use pump.fun data directly
            
            token_data = {
                'address': mint,
                'symbol': pump_data.get('symbol', 'UNKNOWN'),
                'name': pump_data.get('name', 'Unknown'),
                'priceUsd': 0,  # Not on DEX yet
                'liquidity_usd': 0,  # Not on DEX yet
                'volume_24h': 0,
                'price_change_24h': 0,
                'txns_24h_buys': 0,
                'txns_24h_sells': 0,
                'pair_address': '',
                'dex_id': 'pump.fun',
                'url': f"https://pump.fun/{mint}",
                'graduation_source': 'graduating_soon',
                'market_cap': pump_data.get('market_cap', 0),
                'bonding_curve_progress': pump_data.get('bonding_curve_progress', 0),
            }
            
            logger.debug(f"✅ Built early signal data for {token_data['symbol']}")
            return token_data
        
        except Exception as e:
            logger.debug(f"Error building token data: {e}")
            return None

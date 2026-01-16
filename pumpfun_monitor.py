"""
Pump.fun Monitor via whistle.ninja - Free, decentralized, no rate limits
"""

import asyncio
import aiohttp
import os
from typing import Optional, Callable
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

WHISTLE_API = os.getenv('WHISTLE_API', 'https://pump.whistle.ninja')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 60))


class PumpfunMonitor:
    """Monitors pump.fun via whistle.ninja for graduated tokens"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.seen_tokens = set()
    
    async def start(self, callback: Callable):
        """Start monitoring pump.fun graduations via whistle.ninja"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"🟢 Pump.fun monitor started via whistle.ninja (polling every {POLL_INTERVAL}s)")
        logger.info("🎯 Using decentralized API - no rate limits!")
        
        while self.running:
            try:
                await self._fetch_graduations(callback)
                await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error(f"Monitor error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def stop(self):
        """Stop the monitor"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _fetch_graduations(self, callback: Callable):
        """Fetch recently graduated tokens from whistle.ninja"""
        
        # Try multiple endpoint patterns
        endpoints = [
            f"{WHISTLE_API}/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false",
            f"{WHISTLE_API}/api/coins?offset=0&limit=50&sort=created_timestamp&order=DESC",
            f"{WHISTLE_API}/coins/graduated",
            f"{WHISTLE_API}/api/tokens/recent"
        ]
        
        for endpoint in endpoints:
            try:
                async with self.session.get(endpoint, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        if not data:
                            logger.debug(f"Empty response from {endpoint}")
                            continue
                        
                        # Handle different response formats
                        tokens = data if isinstance(data, list) else data.get('coins', data.get('tokens', []))
                        
                        if not tokens:
                            logger.debug(f"No tokens in response from {endpoint}")
                            continue
                        
                        logger.info(f"✅ whistle.ninja responded! Got {len(tokens)} tokens")
                        
                        # Filter for graduated tokens
                        graduated = [
                            t for t in tokens 
                            if t.get('complete') == True or t.get('raydium_pool')
                        ]
                        
                        if graduated:
                            logger.info(f"🎓 Found {len(graduated)} graduated tokens")
                        
                        # Process new graduations
                        for token in graduated:
                            try:
                                mint = token.get('mint')
                                
                                if not mint or mint in self.seen_tokens:
                                    continue
                                
                                self.seen_tokens.add(mint)
                                
                                # Limit seen set size
                                if len(self.seen_tokens) > 1000:
                                    self.seen_tokens = set(list(self.seen_tokens)[-500:])
                                
                                symbol = token.get('symbol', 'UNKNOWN')
                                logger.info(f"🆕 New graduation: {symbol} ({mint[:8]}...)")
                                
                                # Fetch full data from DexScreener
                                token_data = await self._fetch_dex_data(mint, token)
                                
                                if token_data:
                                    await callback(token_data)
                            
                            except Exception as e:
                                logger.debug(f"Error processing token: {e}")
                        
                        # Successfully processed from this endpoint
                        return
                    
                    elif resp.status == 404:
                        logger.debug(f"Endpoint not found: {endpoint}")
                        continue
                    else:
                        logger.warning(f"whistle.ninja returned {resp.status} for {endpoint}")
                        continue
            
            except asyncio.TimeoutError:
                logger.warning(f"Timeout for {endpoint}")
                continue
            except Exception as e:
                logger.debug(f"Error with {endpoint}: {e}")
                continue
        
        # All endpoints failed
        logger.warning("All whistle.ninja endpoints failed, will retry next cycle")
    
    async def _fetch_dex_data(self, mint: str, pump_data: dict) -> Optional[dict]:
        """Fetch token data from DexScreener"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            
            async with self.session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.debug(f"DexScreener returned {resp.status} for {mint}")
                    return None
                
                data = await resp.json()
                pairs = data.get('pairs', [])
                
                if not pairs:
                    logger.debug(f"No pairs found for {mint}")
                    return None
                
                # Get Raydium pair
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
                
                logger.debug(f"✅ Got DEX data for {token_data['symbol']}")
                return token_data
        
        except asyncio.TimeoutError:
            logger.debug(f"DexScreener timeout for {mint}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching DEX data: {e}")
            return None

"""
Pump.fun Monitor - Polls for graduated tokens
"""

import asyncio
import aiohttp
import os
from typing import Optional, Callable
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

PUMPFUN_API = os.getenv('PUMPFUN_API', 'https://frontend-api.pump.fun')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 60))  # Check every 60s


class PumpfunMonitor:
    """Monitors pump.fun for graduated tokens"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.seen_tokens = set()
    
    async def start(self, callback: Callable):
        """
        Start monitoring pump.fun graduations
        
        Args:
            callback: Async function to call with token data
        """
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"🟢 Pump.fun monitor started (polling every {POLL_INTERVAL}s)")
        
        while self.running:
            try:
                await self._fetch_graduations(callback)
                await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error(f"Pump.fun monitor error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def stop(self):
        """Stop the monitor"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _fetch_graduations(self, callback: Callable):
        """Fetch recently graduated tokens"""
        try:
            url = f"{PUMPFUN_API}/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false"
            
            async with self.session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning(f"Pump.fun API returned {resp.status}")
                    return
                
                data = await resp.json()
                
                if not data:
                    logger.debug("No tokens found")
                    return
                
                # Filter for graduated tokens only
                graduated = [t for t in data if t.get('complete') == True or t.get('raydium_pool')]
                
                logger.debug(f"Found {len(graduated)} graduated tokens")
                
                # Process new graduations
                for token in graduated:
                    try:
                        mint = token.get('mint')
                        
                        # Skip if we've seen this token
                        if mint in self.seen_tokens:
                            continue
                        
                        self.seen_tokens.add(mint)
                        
                        # Fetch full pair data from DexScreener
                        token_data = await self._fetch_dex_data(mint, token)
                        
                        if token_data:
                            await callback(token_data)
                    
                    except Exception as e:
                        logger.debug(f"Error processing graduation: {e}")
        
        except asyncio.TimeoutError:
            logger.warning("Pump.fun API timeout")
        except Exception as e:
            logger.error(f"Error fetching graduations: {e}", exc_info=True)
    
    async def _fetch_dex_data(self, mint: str, pump_data: dict) -> Optional[dict]:
        """Fetch token data from DexScreener"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                pairs = data.get('pairs', [])
                
                if not pairs:
                    return None
                
                # Get Raydium pair (graduated tokens)
                raydium_pairs = [p for p in pairs if 'raydium' in p.get('dexId', '').lower()]
                
                if not raydium_pairs:
                    return None
                
                pair = raydium_pairs[0]
                
                # Build token data
                token_data = {
                    'address': mint,
                    'symbol': pair.get('baseToken', {}).get('symbol', 'UNKNOWN'),
                    'name': pair.get('baseToken', {}).get('name', 'Unknown'),
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
                
                return token_data
        
        except Exception as e:
            logger.debug(f"Error fetching DEX data for {mint}: {e}")
            return None

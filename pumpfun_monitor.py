"""
Pump.fun Monitor - Polls for graduated tokens with retry logic
"""

import asyncio
import aiohttp
import os
from typing import Optional, Callable
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

PUMPFUN_API = os.getenv('PUMPFUN_API', 'https://frontend-api.pump.fun')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 90))  # Check every 90s (less aggressive)


class PumpfunMonitor:
    """Monitors pump.fun for graduated tokens"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.seen_tokens = set()
        self.consecutive_failures = 0
    
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
                success = await self._fetch_graduations(callback)
                
                if success:
                    self.consecutive_failures = 0
                else:
                    self.consecutive_failures += 1
                
                # Backoff if multiple failures
                if self.consecutive_failures >= 3:
                    wait_time = min(POLL_INTERVAL * 2, 300)  # Max 5 min
                    logger.warning(f"Multiple failures, backing off to {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    await asyncio.sleep(POLL_INTERVAL)
                    
            except Exception as e:
                logger.error(f"Pump.fun monitor error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def stop(self):
        """Stop the monitor"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _fetch_graduations(self, callback: Callable) -> bool:
        """
        Fetch recently graduated tokens with retry logic
        
        Returns:
            bool: True if successful, False otherwise
        """
        # Try multiple endpoints in order
        endpoints = [
            f"{PUMPFUN_API}/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false",
            f"{PUMPFUN_API}/coins/graduated",
            f"{PUMPFUN_API}/coins/king-of-the-hill"
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://pump.fun/',
            'Origin': 'https://pump.fun'
        }
        
        for endpoint_idx, url in enumerate(endpoints):
            retries = 3
            
            for attempt in range(retries):
                try:
                    async with self.session.get(url, headers=headers, timeout=20) as resp:
                        # Success!
                        if resp.status == 200:
                            data = await resp.json()
                            
                            if not data:
                                logger.debug("No tokens found in response")
                                return True  # Valid response, just empty
                            
                            # Handle different response formats
                            tokens = data if isinstance(data, list) else data.get('coins', [])
                            
                            # Filter for graduated tokens only
                            graduated = [
                                t for t in tokens 
                                if t.get('complete') == True or t.get('raydium_pool')
                            ]
                            
                            if graduated:
                                logger.info(f"✅ Found {len(graduated)} graduated tokens")
                            
                            # Process new graduations
                            for token in graduated:
                                try:
                                    mint = token.get('mint')
                                    
                                    # Skip if we've seen this token
                                    if not mint or mint in self.seen_tokens:
                                        continue
                                    
                                    self.seen_tokens.add(mint)
                                    
                                    # Limit seen_tokens size (prevent memory bloat)
                                    if len(self.seen_tokens) > 1000:
                                        self.seen_tokens = set(list(self.seen_tokens)[-500:])
                                    
                                    logger.info(f"🎓 New graduation: {token.get('symbol', 'UNKNOWN')}")
                                    
                                    # Fetch full pair data from DexScreener
                                    token_data = await self._fetch_dex_data(mint, token)
                                    
                                    if token_data:
                                        await callback(token_data)
                                
                                except Exception as e:
                                    logger.debug(f"Error processing graduation: {e}")
                            
                            return True
                        
                        # Server overloaded (530)
                        elif resp.status == 530:
                            logger.warning(f"Pump.fun overloaded (530), retry {attempt+1}/{retries}")
                            await asyncio.sleep(15)  # Wait before retry
                            continue
                        
                        # Rate limited (429)
                        elif resp.status == 429:
                            logger.warning(f"Rate limited (429), backing off")
                            await asyncio.sleep(60)
                            continue
                        
                        # Other errors
                        else:
                            logger.warning(f"Pump.fun API returned {resp.status}")
                            # Try next endpoint
                            break
                
                except asyncio.TimeoutError:
                    logger.warning(f"Pump.fun API timeout (attempt {attempt+1}/{retries})")
                    await asyncio.sleep(10)
                    continue
                
                except Exception as e:
                    logger.error(f"Error fetching graduations (attempt {attempt+1}/{retries}): {e}")
                    await asyncio.sleep(10)
                    continue
            
            # If we tried all retries for this endpoint, try next endpoint
            logger.debug(f"Endpoint {endpoint_idx+1} failed after {retries} retries, trying next...")
        
        # All endpoints failed
        logger.error("All pump.fun endpoints failed")
        return False
    
    async def _fetch_dex_data(self, mint: str, pump_data: dict) -> Optional[dict]:
        """Fetch token data from DexScreener"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            
            async with self.session.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    logger.debug(f"DexScreener returned {resp.status} for {mint}")
                    return None
                
                data = await resp.json()
                pairs = data.get('pairs', [])
                
                if not pairs:
                    logger.debug(f"No pairs found for {mint}")
                    return None
                
                # Get Raydium pair (graduated tokens use Raydium)
                raydium_pairs = [p for p in pairs if 'raydium' in p.get('dexId', '').lower()]
                
                if not raydium_pairs:
                    logger.debug(f"No Raydium pair found for {mint}")
                    return None
                
                pair = raydium_pairs[0]
                
                # Build token data
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
                
                logger.debug(f"✅ Fetched DEX data for {token_data['symbol']}")
                return token_data
        
        except asyncio.TimeoutError:
            logger.debug(f"DexScreener timeout for {mint}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching DEX data for {mint}: {e}")
            return None
            

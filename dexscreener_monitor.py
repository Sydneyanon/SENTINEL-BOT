"""
DexScreener Monitor - Polls for new Solana tokens
"""

import asyncio
import aiohttp
import os
from typing import Optional, Callable
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

DEXSCREENER_API = os.getenv('DEXSCREENER_API', 'https://api.dexscreener.com/latest/dex')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 45))


class DexScreenerMonitor:
    """Monitors DexScreener for new Solana tokens"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
    
    async def start(self, callback: Callable):
        """
        Start monitoring DexScreener
        
        Args:
            callback: Async function to call with token data
        """
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"🟢 DexScreener monitor started (polling every {POLL_INTERVAL}s)")
        
        while self.running:
            try:
                await self._fetch_latest_profiles(callback)
                await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error(f"DexScreener monitor error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def stop(self):
        """Stop the monitor"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _fetch_latest_profiles(self, callback: Callable):
        """Fetch latest token profiles from DexScreener"""
        try:
            url = f"{DEXSCREENER_API}/token-profiles/latest/v1"
            
            async with self.session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning(f"DexScreener profiles returned {resp.status}")
                    return
                
                data = await resp.json()
                profiles = data if isinstance(data, list) else []
                
                if not profiles:
                    logger.debug("No token profiles found")
                    return
                
                # Filter for Solana tokens
                solana_profiles = [p for p in profiles if p.get("chainId") == "solana"]
                
                logger.debug(f"Found {len(solana_profiles)} Solana profiles")
                
                # Process each profile
                for profile in solana_profiles[:15]:  # Limit to 15 most recent
                    try:
                        token_addr = profile.get("tokenAddress")
                        if token_addr:
                            token_data = await self._fetch_token_pairs(token_addr, profile)
                            if token_data:
                                await callback(token_data)
                    except Exception as e:
                        logger.debug(f"Error processing profile: {e}")
        
        except asyncio.TimeoutError:
            logger.warning("DexScreener API timeout")
        except Exception as e:
            logger.error(f"Error fetching profiles: {e}", exc_info=True)
    
    async def _fetch_token_pairs(self, address: str, profile: dict) -> Optional[dict]:
        """Fetch detailed token pair data"""
        try:
            url = f"{DEXSCREENER_API}/tokens/{address}"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                pairs = data.get('pairs', [])
                
                if not pairs:
                    return None
                
                # Get the pair with highest liquidity
                pairs.sort(key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)
                best_pair = pairs[0]
                
                # Extract relevant data
                token_data = {
                    'address': address,
                    'symbol': best_pair.get('baseToken', {}).get('symbol', 'UNKNOWN'),
                    'name': best_pair.get('baseToken', {}).get('name', 'Unknown'),
                    'priceUsd': float(best_pair.get('priceUsd', 0)),
                    'liquidity_usd': float(best_pair.get('liquidity', {}).get('usd', 0)),
                    'volume_24h': float(best_pair.get('volume', {}).get('h24', 0)),
                    'price_change_24h': float(best_pair.get('priceChange', {}).get('h24', 0)),
                    'txns_24h_buys': int(best_pair.get('txns', {}).get('h24', {}).get('buys', 0)),
                    'txns_24h_sells': int(best_pair.get('txns', {}).get('h24', {}).get('sells', 0)),
                    'pair_address': best_pair.get('pairAddress', ''),
                    'dex_id': best_pair.get('dexId', ''),
                    'url': best_pair.get('url', ''),
                }
                
                return token_data
        
        except Exception as e:
            logger.debug(f"Error fetching pairs for {address}: {e}")
            return None

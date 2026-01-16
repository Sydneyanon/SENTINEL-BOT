"""
Performance Tracker - Monitors posted signals for milestone achievements
"""

import asyncio
import aiohttp
import os
from datetime import datetime
from typing import List, Dict, Optional
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

# Configuration
PERFORMANCE_CHECK_INTERVAL_SEC = int(os.getenv('PERFORMANCE_CHECK_INTERVAL_SEC', 1800))
DEXSCREENER_API = os.getenv('DEXSCREENER_API', 'https://api.dexscreener.com/latest/dex')

# Milestones: 2x, 3x, 5x, 10x, 20x, 30x, 40x, 50x, 100x-1000x
MILESTONES = [2, 3, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]


class PerformanceTracker:
    """Tracks performance of posted signals and sends milestone alerts"""
    
    def __init__(self, db, telegram_publisher):
        self.db = db
        self.telegram = telegram_publisher
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
    
    async def start(self):
        """Start the performance tracking loop"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"📊 Performance tracker started (checks every {PERFORMANCE_CHECK_INTERVAL_SEC}s)")
        
        while self.running:
            try:
                await self._check_all_signals()
                await asyncio.sleep(PERFORMANCE_CHECK_INTERVAL_SEC)
            except Exception as e:
                logger.error(f"Performance tracker error: {e}", exc_info=True)
                await asyncio.sleep(PERFORMANCE_CHECK_INTERVAL_SEC)
    
    async def stop(self):
        """Stop the tracker and cleanup"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _check_all_signals(self):
        """Check all posted signals for milestone achievements"""
        try:
            signals = await self.db.get_active_signals()
            
            if not signals:
                logger.debug("No active signals to track")
                return
            
            logger.info(f"📊 Checking {len(signals)} active signals for milestones...")
            
            for signal in signals:
                try:
                    await self._check_signal_performance(signal)
                    await asyncio.sleep(1)  # Rate limit API calls
                except Exception as e:
                    logger.error(f"Error checking signal {signal['address']}: {e}")
        
        except Exception as e:
            logger.error(f"Error in _check_all_signals: {e}", exc_info=True)
    
    async def _check_signal_performance(self, signal: Dict):
        """Check a single signal for milestone achievements"""
        address = signal['address']
        initial_price = signal['initial_price']
        symbol = signal.get('symbol', 'UNKNOWN')
        posted_milestones = signal.get('posted_milestones', '')
        
        # Get current price from DexScreener
        current_price = await self._fetch_current_price(address)
        
        if not current_price or not initial_price:
            return
        
        # Calculate multiplier
        multiplier = current_price / initial_price
        
        # Check which milestones have been hit
        posted_list = posted_milestones.split(',') if posted_milestones else []
        posted_set = set(int(x) for x in posted_list if x.strip())
        
        for milestone in MILESTONES:
            if multiplier >= milestone and milestone not in posted_set:
                # New milestone hit!
                await self._post_milestone_alert(
                    address=address,
                    symbol=symbol,
                    milestone=milestone,
                    current_price=current_price,
                    multiplier=multiplier
                )
                
                # Update database to mark this milestone as posted
                posted_set.add(milestone)
                new_posted = ','.join(str(x) for x in sorted(posted_set))
                await self.db.update_posted_milestones(address, new_posted)
                
                logger.info(f"✓ {symbol} hit {milestone}x! (${current_price:.8f}, {multiplier:.2f}x)")
    
    async def _fetch_current_price(self, address: str) -> Optional[float]:
        """Fetch current price from DexScreener"""
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
                
                price_usd = best_pair.get('priceUsd')
                if price_usd:
                    return float(price_usd)
                
                return None
        
        except Exception as e:
            logger.debug(f"Error fetching price for {address}: {e}")
            return None
    
    async def _post_milestone_alert(
        self, 
        address: str, 
        symbol: str, 
        milestone: int,
        current_price: float,
        multiplier: float
    ):
        """Post milestone achievement alert to Telegram"""
        try:
            message = self._format_milestone_message(
                symbol=symbol,
                milestone=milestone,
                current_price=current_price,
                multiplier=multiplier,
                address=address
            )
            
            await self.telegram.send_message(message)
            logger.info(f"✓ Posted {milestone}x milestone alert for {symbol}")
        
        except Exception as e:
            logger.error(f"Error posting milestone alert: {e}", exc_info=True)
    
    def _format_milestone_message(
        self,
        symbol: str,
        milestone: int,
        current_price: float,
        multiplier: float,
        address: str
    ) -> str:
        """Format the milestone alert message - clean and professional"""
        
        # Simple tier classification
        if milestone >= 1000:
            tier = "LEGENDARY"
        elif milestone >= 100:
            tier = "MAJOR"
        elif milestone >= 50:
            tier = "SIGNIFICANT"
        else:
            tier = "MILESTONE"
        
        message = f"""
**{milestone}x {tier} REACHED**

Token: ${symbol}
Multiplier: {multiplier:.2f}x
Current Price: ${current_price:.10f}

[View Chart](https://dexscreener.com/solana/{address})
""".strip()
        
        return message

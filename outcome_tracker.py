"""
Win Rate Tracker - Evaluates signal outcomes and calculates performance metrics
"""

import asyncio
import aiohttp
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from loguru import logger
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

DEXSCREENER_API = os.getenv('DEXSCREENER_API', 'https://api.dexscreener.com/latest/dex')
OUTCOME_CHECK_INTERVAL_SEC = int(os.getenv('OUTCOME_CHECK_INTERVAL_SEC', 3600))

# Win/Loss Definitions
WIN_THRESHOLD = int(os.getenv('WIN_THRESHOLD', 100))  # +100% (2x) = WIN
LOSS_THRESHOLD = int(os.getenv('LOSS_THRESHOLD', -50))  # -50% = LOSS
EVALUATION_WINDOW_HOURS = int(os.getenv('EVALUATION_WINDOW_HOURS', 24))


class Outcome(Enum):
    """Signal outcome types"""
    PENDING = "pending"
    WIN = "win"
    LOSS = "loss"
    EXPIRED = "expired"  # Didn't hit win/loss within timeframe


class OutcomeTracker:
    """Tracks and evaluates signal outcomes for win rate calculation"""
    
    def __init__(self, db):
        self.db = db
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
    
    async def start(self):
        """Start the outcome tracking loop"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"📊 Outcome tracker started (checks every {OUTCOME_CHECK_INTERVAL_SEC}s)")
        logger.info(f"📊 Win definition: +{WIN_THRESHOLD}% within {EVALUATION_WINDOW_HOURS}h")
        logger.info(f"📊 Loss definition: {LOSS_THRESHOLD}% or timeout")
        
        while self.running:
            try:
                await self._check_all_pending_signals()
                await asyncio.sleep(OUTCOME_CHECK_INTERVAL_SEC)
            except Exception as e:
                logger.error(f"Outcome tracker error: {e}", exc_info=True)
                await asyncio.sleep(OUTCOME_CHECK_INTERVAL_SEC)
    
    async def stop(self):
        """Stop the tracker and cleanup"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _check_all_pending_signals(self):
        """Check all pending signals for outcomes"""
        try:
            pending = await self.db.get_pending_outcomes()
            
            if not pending:
                logger.debug("No pending outcomes to check")
                return
            
            logger.info(f"📊 Checking {len(pending)} pending signals for outcomes...")
            
            for signal in pending:
                try:
                    await self._evaluate_signal_outcome(signal)
                    await asyncio.sleep(1)  # Rate limit
                except Exception as e:
                    logger.error(f"Error evaluating {signal['address']}: {e}")
        
        except Exception as e:
            logger.error(f"Error in _check_all_pending_signals: {e}", exc_info=True)
    
    async def _evaluate_signal_outcome(self, signal: Dict):
        """Evaluate a single signal's outcome"""
        address = signal['address']
        symbol = signal.get('symbol', 'UNKNOWN')
        initial_price = signal['initial_price']
        posted_at = datetime.fromisoformat(signal['posted_at'])
        
        # Check if evaluation window has expired
        time_elapsed = datetime.now() - posted_at
        window_expired = time_elapsed.total_seconds() / 3600 > EVALUATION_WINDOW_HOURS
        
        # Fetch current price
        current_price = await self._fetch_current_price(address)
        
        if not current_price or not initial_price:
            return
        
        # Calculate performance
        gain_pct = ((current_price - initial_price) / initial_price) * 100
        
        # Also track peak price for historical reference
        peak_price = await self.db.get_peak_price(address) or current_price
        peak_price = max(peak_price, current_price)
        await self.db.update_peak_price(address, peak_price)
        
        peak_gain_pct = ((peak_price - initial_price) / initial_price) * 100
        
        # Determine outcome
        outcome = None
        
        if gain_pct >= WIN_THRESHOLD:
            # Hit win target!
            outcome = Outcome.WIN
            outcome_price = current_price
            outcome_gain = gain_pct
            reason = f"Hit +{WIN_THRESHOLD}% target"
        
        elif gain_pct <= LOSS_THRESHOLD:
            # Hit loss threshold
            outcome = Outcome.LOSS
            outcome_price = current_price
            outcome_gain = gain_pct
            reason = f"Dropped below {LOSS_THRESHOLD}%"
        
        elif window_expired:
            # Time ran out
            if gain_pct > 0:
                outcome = Outcome.EXPIRED
                outcome_price = current_price
                outcome_gain = gain_pct
                reason = f"Expired at +{gain_pct:.1f}%"
            else:
                outcome = Outcome.LOSS
                outcome_price = current_price
                outcome_gain = gain_pct
                reason = f"Expired at {gain_pct:.1f}%"
        
        # Save outcome if determined
        if outcome:
            await self.db.save_outcome(
                address=address,
                outcome=outcome.value,
                outcome_price=outcome_price,
                outcome_gain=outcome_gain,
                peak_gain=peak_gain_pct,
                evaluated_at=datetime.now().isoformat(),
                reason=reason
            )
            
            logger.info(f"📊 {symbol}: {outcome.value.upper()} ({outcome_gain:+.1f}%, peak: {peak_gain_pct:+.1f}%)")
    
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
                
                pairs.sort(key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)
                best_pair = pairs[0]
                
                price_usd = best_pair.get('priceUsd')
                if price_usd:
                    return float(price_usd)
                
                return None
        
        except Exception as e:
            logger.debug(f"Error fetching price for {address}: {e}")
            return None
    
    async def get_win_rate_stats(self, days: Optional[int] = None) -> Dict:
        """
        Calculate win rate statistics
        
        Args:
            days: Filter to last N days, or None for all-time
        
        Returns:
            Dict with comprehensive stats
        """
        outcomes = await self.db.get_outcomes(days=days)
        
        if not outcomes:
            return {
                'total_signals': 0,
                'win_rate': 0,
                'avg_gain_on_wins': 0,
                'avg_loss_on_losses': 0,
                'best_performer': None,
                'worst_performer': None,
                'pending': 0
            }
        
        wins = [o for o in outcomes if o['outcome'] == Outcome.WIN.value]
        losses = [o for o in outcomes if o['outcome'] in [Outcome.LOSS.value, Outcome.EXPIRED.value]]
        pending = await self.db.count_pending_outcomes()
        
        total = len(wins) + len(losses)
        win_rate = (len(wins) / total * 100) if total > 0 else 0
        
        avg_gain_on_wins = sum(w['outcome_gain'] for w in wins) / len(wins) if wins else 0
        avg_loss_on_losses = sum(l['outcome_gain'] for l in losses) / len(losses) if losses else 0
        
        # Best and worst performers
        all_outcomes = wins + losses
        if all_outcomes:
            best = max(all_outcomes, key=lambda x: x['peak_gain'])
            worst = min(all_outcomes, key=lambda x: x['outcome_gain'])
        else:
            best = worst = None
        
        return {
            'total_signals': total,
            'wins': len(wins),
            'losses': len(losses),
            'pending': pending,
            'win_rate': win_rate,
            'avg_gain_on_wins': avg_gain_on_wins,
            'avg_loss_on_losses': avg_loss_on_losses,
            'best_performer': best,
            'worst_performer': worst,
            'timeframe': f'{days}d' if days else 'all-time'
        }

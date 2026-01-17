"""
Performance Tracker - Monitors posted signals for milestone achievements AND metric changes
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
PERFORMANCE_CHECK_INTERVAL_SEC = int(os.getenv('PERFORMANCE_CHECK_INTERVAL_SEC', 300))  # Check every 5min
DEXSCREENER_API = os.getenv('DEXSCREENER_API', 'https://api.dexscreener.com/latest/dex')
HELIUS_API_KEY = os.getenv('HELIUS_API_KEY', '')
BIRDEYE_API_KEY = os.getenv('BIRDEYE_API_KEY', '')

# Milestones: 2x, 3x, 5x, 10x, 20x, 30x, 40x, 50x, 100x-1000x
MILESTONES = [2, 3, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]

# Event thresholds (only post when these are exceeded)
THRESHOLDS = {
    'holder_increase': 20,  # +20% holders
    'holder_decrease': 15,  # -15% holders
    'volume_spike': 50,     # +50% volume
    'volume_crash': 50,     # -50% volume
    'liquidity_add': 30,    # +30% liquidity
    'liquidity_pull': 30,   # -30% liquidity
    'price_pump': 25,       # +25% price (5min)
    'price_dump': 20,       # -20% price (5min)
}


class PerformanceTracker:
    """Tracks performance of posted signals - milestones + event-driven updates"""
    
    def __init__(self, db, telegram_publisher):
        self.db = db
        self.telegram = telegram_publisher
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        
        # Store previous state for each token
        self.token_states: Dict[str, Dict] = {}
    
    async def start(self):
        """Start the performance tracking loop"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"📊 Performance tracker started (checks every {PERFORMANCE_CHECK_INTERVAL_SEC}s)")
        logger.info("📊 Event-driven updates: holders, volume, liquidity, price changes")
        logger.info("🏆 Instant WIN confirmation on 2x milestone")
        
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
    
    async def track_token(self, token_address: str):
        """Add a new token to tracking (called when signal is posted)"""
        logger.info(f"📊 Now tracking: {token_address}")
    
    async def _check_all_signals(self):
        """Check all posted signals for milestones and metric changes"""
        try:
            signals = await self.db.get_active_signals()
            
            if not signals:
                logger.debug("No active signals to track")
                return
            
            logger.debug(f"📊 Checking {len(signals)} active signals...")
            
            for signal in signals:
                try:
                    await self._check_signal(signal)
                    await asyncio.sleep(1)  # Rate limit API calls
                except Exception as e:
                    logger.error(f"Error checking signal {signal.get('address', 'unknown')}: {e}")
        
        except Exception as e:
            logger.error(f"Error in _check_all_signals: {e}", exc_info=True)
    
    async def _check_signal(self, signal: Dict):
        """Check a single signal for milestones AND metric changes"""
        address = signal['address']
        
        # Fetch current metrics
        current_metrics = await self._fetch_token_metrics(address)
        
        if not current_metrics:
            return
        
        # Check for milestone achievements (includes instant WIN on 2x)
        await self._check_milestones(signal, current_metrics)
        
        # Check for significant metric changes (event-driven)
        await self._check_metric_changes(signal, current_metrics)
        
        # Update stored state
        self.token_states[address] = current_metrics
    
    async def _fetch_token_metrics(self, address: str) -> Optional[Dict]:
        """Fetch comprehensive metrics for a token"""
        try:
            # Get price, volume, liquidity from DexScreener
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
                pair = pairs[0]
                
                metrics = {
                    'price': float(pair.get('priceUsd', 0)),
                    'volume_24h': float(pair.get('volume', {}).get('h24', 0)),
                    'volume_5m': float(pair.get('volume', {}).get('m5', 0)),
                    'liquidity_usd': float(pair.get('liquidity', {}).get('usd', 0)),
                    'price_change_5m': float(pair.get('priceChange', {}).get('m5', 0)),
                    'txns_5m_buys': int(pair.get('txns', {}).get('m5', {}).get('buys', 0)),
                    'txns_5m_sells': int(pair.get('txns', {}).get('m5', {}).get('sells', 0)),
                    'timestamp': datetime.now().timestamp()
                }
                
                # Try to get holder count from Birdeye or Helius
                holder_count = await self._fetch_holder_count(address)
                if holder_count:
                    metrics['holders'] = holder_count
                
                return metrics
        
        except Exception as e:
            logger.debug(f"Error fetching metrics for {address}: {e}")
            return None
    
    async def _fetch_holder_count(self, address: str) -> Optional[int]:
        """Fetch holder count from Birdeye v3 API"""
        if not BIRDEYE_API_KEY:
            return None
        
        try:
            url = f"https://public-api.birdeye.so/defi/v3/token/holder?address={address}"
            headers = {
                "X-API-KEY": BIRDEYE_API_KEY,
                "x-chain": "solana"
            }
            
            async with self.session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                
                # v3 API structure: data.total
                holder_count = data.get('data', {}).get('total', 0)
                
                return int(holder_count) if holder_count else None
        
        except Exception as e:
            logger.debug(f"Error fetching holder count: {e}")
            return None
    
    async def _check_milestones(self, signal: Dict, current_metrics: Dict):
        """Check for milestone achievements and IMMEDIATELY mark 2x as WIN"""
        address = signal['address']
        initial_price = signal['initial_price']
        symbol = signal.get('symbol', 'UNKNOWN')
        posted_milestones = signal.get('posted_milestones', '')
        
        current_price = current_metrics.get('price', 0)
        
        if not current_price or not initial_price:
            return
        
        # Calculate multiplier and gain percentage
        multiplier = current_price / initial_price
        gain_pct = (multiplier - 1) * 100
        
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
                
                # 🏆 IMMEDIATE WIN CONFIRMATION when hitting 2x
                if milestone == 2:
                    # Mark as WIN in database immediately
                    await self.db.save_outcome(
                        address=address,
                        outcome='win',
                        outcome_price=current_price,
                        outcome_gain=gain_pct,
                        peak_gain=gain_pct,  # At 2x, this is the peak so far
                        evaluated_at=datetime.now().isoformat(),
                        reason='Hit 2x target'
                    )
                    logger.success(f"🏆 {symbol} CONFIRMED WIN at 2x ({gain_pct:.1f}%)")
                
                # Update database
                posted_set.add(milestone)
                new_posted = ','.join(str(x) for x in sorted(posted_set))
                await self.db.update_posted_milestones(address, new_posted)
                
                logger.info(f"✓ {symbol} hit {milestone}x! (${current_price:.8f}, {multiplier:.2f}x)")
    
    async def _check_metric_changes(self, signal: Dict, current_metrics: Dict):
        """Check for significant metric changes (event-driven updates)"""
        address = signal['address']
        symbol = signal.get('symbol', 'UNKNOWN')
        
        # Get previous state
        prev_metrics = self.token_states.get(address)
        
        if not prev_metrics:
            # First time seeing this token, no comparison yet
            return
        
        events = []
        
        # Check holder change
        if 'holders' in current_metrics and 'holders' in prev_metrics:
            holder_change_pct = ((current_metrics['holders'] - prev_metrics['holders']) / prev_metrics['holders']) * 100
            
            if holder_change_pct >= THRESHOLDS['holder_increase']:
                events.append({
                    'type': 'holder_increase',
                    'emoji': '👥',
                    'message': f"Holders: {prev_metrics['holders']:,} → {current_metrics['holders']:,} (+{holder_change_pct:.1f}%)"
                })
            elif holder_change_pct <= -THRESHOLDS['holder_decrease']:
                events.append({
                    'type': 'holder_decrease',
                    'emoji': '⚠️',
                    'message': f"Holders: {prev_metrics['holders']:,} → {current_metrics['holders']:,} ({holder_change_pct:.1f}%)"
                })
        
        # Check volume change
        if current_metrics['volume_24h'] > 0 and prev_metrics['volume_24h'] > 0:
            volume_change_pct = ((current_metrics['volume_24h'] - prev_metrics['volume_24h']) / prev_metrics['volume_24h']) * 100
            
            if volume_change_pct >= THRESHOLDS['volume_spike']:
                events.append({
                    'type': 'volume_spike',
                    'emoji': '📈',
                    'message': f"Volume: ${prev_metrics['volume_24h']:,.0f} → ${current_metrics['volume_24h']:,.0f} (+{volume_change_pct:.0f}%)"
                })
            elif volume_change_pct <= -THRESHOLDS['volume_crash']:
                events.append({
                    'type': 'volume_crash',
                    'emoji': '⚠️',
                    'message': f"Volume: ${prev_metrics['volume_24h']:,.0f} → ${current_metrics['volume_24h']:,.0f} ({volume_change_pct:.0f}%)"
                })
        
        # Check liquidity change
        if current_metrics['liquidity_usd'] > 0 and prev_metrics['liquidity_usd'] > 0:
            liq_change_pct = ((current_metrics['liquidity_usd'] - prev_metrics['liquidity_usd']) / prev_metrics['liquidity_usd']) * 100
            
            if liq_change_pct >= THRESHOLDS['liquidity_add']:
                events.append({
                    'type': 'liquidity_add',
                    'emoji': '💧',
                    'message': f"Liquidity: ${prev_metrics['liquidity_usd']:,.0f} → ${current_metrics['liquidity_usd']:,.0f} (+{liq_change_pct:.0f}%)"
                })
            elif liq_change_pct <= -THRESHOLDS['liquidity_pull']:
                events.append({
                    'type': 'liquidity_pull',
                    'emoji': '🚨',
                    'message': f"Liquidity: ${prev_metrics['liquidity_usd']:,.0f} → ${current_metrics['liquidity_usd']:,.0f} ({liq_change_pct:.0f}%)"
                })
        
        # Check price movement (5min)
        price_change_5m = current_metrics.get('price_change_5m', 0)
        if price_change_5m >= THRESHOLDS['price_pump']:
            events.append({
                'type': 'price_pump',
                'emoji': '🚀',
                'message': f"Price: +{price_change_5m:.1f}% in 5min"
            })
        elif price_change_5m <= -THRESHOLDS['price_dump']:
            events.append({
                'type': 'price_dump',
                'emoji': '📉',
                'message': f"Price: {price_change_5m:.1f}% in 5min"
            })
        
        # If significant events detected, post update
        if events:
            await self._post_metric_update(address, symbol, events, current_metrics)
    
    async def _post_metric_update(
        self,
        address: str,
        symbol: str,
        events: List[Dict],
        current_metrics: Dict
    ):
        """Post an event-driven metric update"""
        try:
            # Determine overall status
            positive_events = ['holder_increase', 'volume_spike', 'liquidity_add', 'price_pump']
            negative_events = ['holder_decrease', 'volume_crash', 'liquidity_pull', 'price_dump']
            
            pos_count = sum(1 for e in events if e['type'] in positive_events)
            neg_count = sum(1 for e in events if e['type'] in negative_events)
            
            if pos_count > neg_count:
                status = "🔥 LOOKING GOOD"
            elif neg_count > pos_count:
                status = "⚠️ CAUTION"
            else:
                status = "➡️ MIXED SIGNALS"
            
            # Build message
            event_lines = '\n'.join([f"{e['emoji']} {e['message']}" for e in events])
            
            message = f"""**📊 ${symbol} ALERT**

{event_lines}

Status: {status}
Price: ${current_metrics['price']:.10f}

[View Chart](https://dexscreener.com/solana/{address})
""".strip()
            
            await self.telegram.send_message(message)
            logger.info(f"✓ Posted metric update for {symbol} ({len(events)} events)")
        
        except Exception as e:
            logger.error(f"Error posting metric update: {e}", exc_info=True)
    
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
            # Emoji based on milestone
            if milestone >= 100:
                emoji = "🌙"
            elif milestone >= 50:
                emoji = "💎"
            elif milestone >= 10:
                emoji = "🔥"
            elif milestone >= 5:
                emoji = "🚀"
            elif milestone == 2:
                emoji = "🏆"  # Special for WIN confirmation
            else:
                emoji = "📈"
            
            # Special message for 2x WIN
            if milestone == 2:
                message = f"""**{emoji} ${symbol} HIT 2X - WIN CONFIRMED!**

Multiplier: {multiplier:.2f}x
Current Price: ${current_price:.10f}

🏆 This signal is now marked as a WIN

[View Chart](https://dexscreener.com/solana/{address})
""".strip()
            else:
                message = f"""**{emoji} ${symbol} HIT {milestone}X!**

Multiplier: {multiplier:.2f}x
Current Price: ${current_price:.10f}

[View Chart](https://dexscreener.com/solana/{address})
""".strip()
            
            await self.telegram.send_message(message)
            logger.info(f"✓ Posted {milestone}x milestone alert for {symbol}")
        
        except Exception as e:
            logger.error(f"Error posting milestone alert: {e}", exc_info=True)

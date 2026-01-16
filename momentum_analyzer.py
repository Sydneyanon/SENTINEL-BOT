"""
Momentum Analyzer - Calculates volume/price momentum to suggest sell points
"""

import asyncio
import aiohttp
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from loguru import logger
from collections import deque
from dotenv import load_dotenv

load_dotenv()

DEXSCREENER_API = os.getenv('DEXSCREENER_API', 'https://api.dexscreener.com/latest/dex')
MOMENTUM_CHECK_INTERVAL_SEC = int(os.getenv('MOMENTUM_CHECK_INTERVAL_SEC', 900))


class MomentumDataPoint:
    """Single data point for momentum calculation"""
    def __init__(self, timestamp: datetime, price: float, volume_24h: float, 
                 txns_buys: int, txns_sells: int):
        self.timestamp = timestamp
        self.price = price
        self.volume_24h = volume_24h
        self.txns_buys = txns_buys
        self.txns_sells = txns_sells
        self.buy_sell_ratio = txns_buys / max(txns_sells, 1)


class MomentumAnalyzer:
    """Analyzes price and volume momentum to suggest sell points"""
    
    def __init__(self, db, telegram_publisher):
        self.db = db
        self.telegram = telegram_publisher
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        
        # Store recent data points for each token (last 10 checks = ~2.5 hours)
        self.momentum_history: Dict[str, deque] = {}
    
    async def start(self):
        """Start the momentum analyzer loop"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"📊 Momentum analyzer started (checks every {MOMENTUM_CHECK_INTERVAL_SEC}s)")
        
        while self.running:
            try:
                await self._analyze_all_signals()
                await asyncio.sleep(MOMENTUM_CHECK_INTERVAL_SEC)
            except Exception as e:
                logger.error(f"Momentum analyzer error: {e}", exc_info=True)
                await asyncio.sleep(MOMENTUM_CHECK_INTERVAL_SEC)
    
    async def stop(self):
        """Stop the analyzer and cleanup"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _analyze_all_signals(self):
        """Analyze momentum for all active signals"""
        try:
            signals = await self.db.get_active_signals()
            
            if not signals:
                return
            
            logger.debug(f"📊 Analyzing momentum for {len(signals)} signals...")
            
            for signal in signals:
                try:
                    await self._analyze_signal_momentum(signal)
                    await asyncio.sleep(1)  # Rate limit
                except Exception as e:
                    logger.error(f"Error analyzing {signal['address']}: {e}")
        
        except Exception as e:
            logger.error(f"Error in _analyze_all_signals: {e}", exc_info=True)
    
    async def _analyze_signal_momentum(self, signal: Dict):
        """Analyze a single signal's momentum"""
        address = signal['address']
        symbol = signal.get('symbol', 'UNKNOWN')
        initial_price = signal['initial_price']
        
        # Fetch current market data
        market_data = await self._fetch_market_data(address)
        
        if not market_data:
            return
        
        current_price = market_data['priceUsd']
        multiplier = current_price / initial_price
        
        # Only analyze signals that have made gains (>1.5x minimum)
        if multiplier < 1.5:
            return
        
        # Store data point
        data_point = MomentumDataPoint(
            timestamp=datetime.now(),
            price=current_price,
            volume_24h=market_data['volume24h'],
            txns_buys=market_data['txns_buys'],
            txns_sells=market_data['txns_sells']
        )
        
        # Initialize history for this token if needed
        if address not in self.momentum_history:
            self.momentum_history[address] = deque(maxlen=10)
        
        self.momentum_history[address].append(data_point)
        
        # Need at least 3 data points to calculate trends
        if len(self.momentum_history[address]) < 3:
            return
        
        # Calculate momentum indicators
        momentum_analysis = self._calculate_momentum(address)
        
        # Check if we should issue a sell recommendation
        sell_signal = self._evaluate_sell_signal(momentum_analysis, multiplier)
        
        if sell_signal:
            # Check if we've already sent this type of signal
            history = await self.db.get_sell_signal_history(address)
            if history and history['signal_type'] == sell_signal['type']:
                return  # Don't spam same signal
            
            await self._post_sell_recommendation(
                address=address,
                symbol=symbol,
                multiplier=multiplier,
                current_price=current_price,
                analysis=momentum_analysis,
                recommendation=sell_signal
            )
            
            # Mark that we've sent a sell signal for this token
            await self.db.mark_sell_signal_sent(address, sell_signal['type'])
    
    async def _fetch_market_data(self, address: str) -> Optional[Dict]:
        """Fetch current market data from DexScreener"""
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
                
                return {
                    'priceUsd': float(best_pair.get('priceUsd', 0)),
                    'volume24h': float(best_pair.get('volume', {}).get('h24', 0)),
                    'txns_buys': int(best_pair.get('txns', {}).get('h24', {}).get('buys', 0)),
                    'txns_sells': int(best_pair.get('txns', {}).get('h24', {}).get('sells', 0)),
                    'liquidity': float(best_pair.get('liquidity', {}).get('usd', 0))
                }
        
        except Exception as e:
            logger.debug(f"Error fetching market data for {address}: {e}")
            return None
    
    def _calculate_momentum(self, address: str) -> Dict:
        """Calculate momentum indicators from historical data"""
        history = list(self.momentum_history[address])
        
        if len(history) < 3:
            return {}
        
        # Price momentum (velocity and acceleration)
        price_changes = []
        for i in range(1, len(history)):
            change_pct = ((history[i].price - history[i-1].price) / history[i-1].price) * 100
            price_changes.append(change_pct)
        
        avg_price_velocity = sum(price_changes) / len(price_changes)
        
        # Price acceleration (is momentum slowing down?)
        price_acceleration = 0
        if len(price_changes) >= 2:
            recent_velocity = sum(price_changes[-2:]) / 2
            older_velocity = sum(price_changes[:2]) / 2
            price_acceleration = recent_velocity - older_velocity
        
        # Volume trend
        volume_changes = []
        for i in range(1, len(history)):
            change_pct = ((history[i].volume_24h - history[i-1].volume_24h) / max(history[i-1].volume_24h, 1)) * 100
            volume_changes.append(change_pct)
        
        avg_volume_trend = sum(volume_changes) / len(volume_changes)
        
        # Buy/Sell pressure trend
        recent_ratio = history[-1].buy_sell_ratio
        older_ratio = history[0].buy_sell_ratio
        ratio_change = ((recent_ratio - older_ratio) / max(older_ratio, 0.1)) * 100
        
        return {
            'price_velocity': avg_price_velocity,
            'price_acceleration': price_acceleration,
            'volume_trend': avg_volume_trend,
            'buy_sell_ratio': recent_ratio,
            'ratio_change': ratio_change,
            'data_points': len(history)
        }
    
    def _evaluate_sell_signal(self, analysis: Dict, multiplier: float) -> Optional[Dict]:
        """Evaluate whether to issue a sell recommendation"""
        
        if not analysis:
            return None
        
        price_vel = analysis['price_velocity']
        price_accel = analysis['price_acceleration']
        volume_trend = analysis['volume_trend']
        ratio_change = analysis['ratio_change']
        buy_sell_ratio = analysis['buy_sell_ratio']
        
        # STRONG SELL SIGNALS (Take Profits Now)
        
        # 1. Price momentum dying + volume declining
        if price_accel < -5 and volume_trend < -20:
            return {
                'type': 'STRONG_SELL',
                'action': 'Take 75-100% profits',
                'reason': 'Momentum fading with declining volume',
                'confidence': 'High'
            }
        
        # 2. Sell pressure increasing rapidly
        if ratio_change < -30 and buy_sell_ratio < 1.0:
            return {
                'type': 'STRONG_SELL',
                'action': 'Take 75-100% profits',
                'reason': 'Sell pressure overwhelming buys',
                'confidence': 'High'
            }
        
        # 3. Price stalling at high multiplier
        if multiplier > 20 and abs(price_vel) < 2 and volume_trend < -15:
            return {
                'type': 'STRONG_SELL',
                'action': 'Take 75-100% profits',
                'reason': 'Price stagnating after large run',
                'confidence': 'High'
            }
        
        # MODERATE SELL SIGNALS (Partial Profits)
        
        # 4. Momentum slowing but not critical
        if price_accel < -3 and volume_trend < -10:
            return {
                'type': 'PARTIAL_SELL',
                'action': 'Take 25-50% profits',
                'reason': 'Momentum slowing, secure some gains',
                'confidence': 'Medium'
            }
        
        # 5. High gain with weakening volume
        if multiplier > 10 and volume_trend < -15:
            return {
                'type': 'PARTIAL_SELL',
                'action': 'Take 25-50% profits',
                'reason': 'Strong gains but volume declining',
                'confidence': 'Medium'
            }
        
        # 6. Buy pressure weakening
        if ratio_change < -20 and buy_sell_ratio < 1.5:
            return {
                'type': 'PARTIAL_SELL',
                'action': 'Take 25-50% profits',
                'reason': 'Buy pressure weakening',
                'confidence': 'Medium'
            }
        
        # HOLD SIGNALS (Positive momentum)
        
        # Strong momentum continuing
        if price_vel > 5 and volume_trend > 0:
            return {
                'type': 'HOLD',
                'action': 'Continue holding',
                'reason': 'Strong momentum with healthy volume',
                'confidence': 'High'
            }
        
        return None  # No clear signal
    
    async def _post_sell_recommendation(
        self,
        address: str,
        symbol: str,
        multiplier: float,
        current_price: float,
        analysis: Dict,
        recommendation: Dict
    ):
        """Post sell recommendation to Telegram"""
        try:
            message = self._format_sell_message(
                symbol=symbol,
                multiplier=multiplier,
                current_price=current_price,
                analysis=analysis,
                recommendation=recommendation,
                address=address
            )
            
            await self.telegram.send_message(message)
            logger.info(f"✓ Posted {recommendation['type']} signal for {symbol}")
        
        except Exception as e:
            logger.error(f"Error posting sell recommendation: {e}", exc_info=True)
    
    def _format_sell_message(
        self,
        symbol: str,
        multiplier: float,
        current_price: float,
        analysis: Dict,
        recommendation: Dict,
        address: str
    ) -> str:
        """Format the sell recommendation message"""
        
        signal_type = recommendation['type']
        action = recommendation['action']
        reason = recommendation['reason']
        confidence = recommendation['confidence']
        
        # Header based on signal type
        if signal_type == 'STRONG_SELL':
            header = "⚠️ SELL SIGNAL"
        elif signal_type == 'PARTIAL_SELL':
            header = "📊 PROFIT TAKING"
        else:
            header = "✅ HOLD"
        
        message = f"""
**{header}**

Token: ${symbol}
Current: {multiplier:.2f}x | ${current_price:.10f}

**Recommendation:** {action}
**Reason:** {reason}
**Confidence:** {confidence}

**Analysis:**
- Price momentum: {analysis['price_velocity']:+.1f}%
- Volume trend: {analysis['volume_trend']:+.1f}%
- Buy/Sell ratio: {analysis['buy_sell_ratio']:.2f}

[View Chart](https://dexscreener.com/solana/{address})
""".strip()
        
        return message

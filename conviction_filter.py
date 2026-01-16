"""
Conviction Filter - Scores tokens based on quality metrics
"""

from typing import Tuple, List, Dict
from loguru import logger


class ConvictionFilter:
    """Calculates conviction score (0-100) for tokens"""
    
    def __init__(self):
        # Scoring thresholds
        self.thresholds = {
            'liquidity': {
                'excellent': 50000,  # $50k+
                'good': 20000,       # $20k+
                'minimum': 5000      # $5k+
            },
            'volume': {
                'excellent': 100000,  # $100k+
                'good': 50000,        # $50k+
                'minimum': 10000      # $10k+
            },
            'price_change': {
                'strong_pump': 50,    # +50%+
                'moderate': 20,       # +20%+
                'weak': 10            # +10%+
            },
            'txns_ratio': {
                'bullish': 1.5,       # 1.5x more buys than sells
                'neutral': 1.0        # Equal buys/sells
            }
        }
    
    def calculate_conviction_score(self, token_data: Dict) -> Tuple[float, List[str]]:
        """
        Calculate conviction score (0-100) for a token
        
        Args:
            token_data: Token information dict
            
        Returns:
            (score, reasons) - Score and list of reason strings
        """
        score = 0
        reasons = []
        
        # Extract data
        liquidity = token_data.get('liquidity_usd', 0)
        volume = token_data.get('volume_24h', 0)
        price_change = token_data.get('price_change_24h', 0)
        buys = token_data.get('txns_24h_buys', 0)
        sells = token_data.get('txns_24h_sells', 0)
        signal_type = token_data.get('signal_type', 'graduated')
        market_cap = token_data.get('market_cap', 0)
        
        # 1. LIQUIDITY SCORE (0-25 points)
        liq_score, liq_reason = self._score_liquidity(liquidity)
        score += liq_score
        if liq_reason:
            reasons.append(liq_reason)
        
        # 2. VOLUME SCORE (0-20 points)
        vol_score, vol_reason = self._score_volume(volume)
        score += vol_score
        if vol_reason:
            reasons.append(vol_reason)
        
        # 3. PRICE MOMENTUM (0-15 points)
        price_score, price_reason = self._score_price_change(price_change)
        score += price_score
        if price_reason:
            reasons.append(price_reason)
        
        # 4. BUY/SELL RATIO (0-15 points)
        ratio_score, ratio_reason = self._score_buy_sell_ratio(buys, sells)
        score += ratio_score
        if ratio_reason:
            reasons.append(ratio_reason)
        
        # 5. SIGNAL TYPE BONUS (0-10 points)
        type_score, type_reason = self._score_signal_type(signal_type)
        score += type_score
        if type_reason:
            reasons.append(type_reason)
        
        # 6. MARKET CAP (0-10 points for graduating tokens)
        if signal_type == 'graduating' and market_cap:
            mc_score, mc_reason = self._score_market_cap(market_cap)
            score += mc_score
            if mc_reason:
                reasons.append(mc_reason)
        
        # 7. VOLUME/LIQUIDITY RATIO (0-5 points)
        if liquidity > 0:
            vol_liq_ratio = volume / liquidity
            if vol_liq_ratio > 2:  # Volume 2x+ liquidity = active trading
                score += 5
                reasons.append(f"High trading activity (vol/liq: {vol_liq_ratio:.1f}x) (+5)")
        
        return min(score, 100), reasons
    
    def _score_liquidity(self, liquidity: float) -> Tuple[float, str]:
        """Score liquidity (0-25 points)"""
        if liquidity >= self.thresholds['liquidity']['excellent']:
            return 25, f"💧 Excellent liquidity (${liquidity:,.0f}) (+25)"
        elif liquidity >= self.thresholds['liquidity']['good']:
            return 20, f"💧 Good liquidity (${liquidity:,.0f}) (+20)"
        elif liquidity >= self.thresholds['liquidity']['minimum']:
            return 15, f"💧 Adequate liquidity (${liquidity:,.0f}) (+15)"
        else:
            return 0, f"⚠️ Low liquidity (${liquidity:,.0f})"
    
    def _score_volume(self, volume: float) -> Tuple[float, str]:
        """Score 24h volume (0-20 points)"""
        if volume >= self.thresholds['volume']['excellent']:
            return 20, f"📊 Excellent volume (${volume:,.0f}) (+20)"
        elif volume >= self.thresholds['volume']['good']:
            return 15, f"📊 Strong volume (${volume:,.0f}) (+15)"
        elif volume >= self.thresholds['volume']['minimum']:
            return 10, f"📊 Decent volume (${volume:,.0f}) (+10)"
        else:
            return 0, f"⚠️ Low volume (${volume:,.0f})"
    
    def _score_price_change(self, price_change: float) -> Tuple[float, str]:
        """Score 24h price change (0-15 points)"""
        if price_change >= self.thresholds['price_change']['strong_pump']:
            return 15, f"🚀 Strong pump ({price_change:+.1f}%) (+15)"
        elif price_change >= self.thresholds['price_change']['moderate']:
            return 10, f"📈 Moderate pump ({price_change:+.1f}%) (+10)"
        elif price_change >= self.thresholds['price_change']['weak']:
            return 5, f"📈 Positive momentum ({price_change:+.1f}%) (+5)"
        elif price_change >= -10:
            return 0, f"Stable price ({price_change:+.1f}%)"
        else:
            return 0, f"⚠️ Price declining ({price_change:.1f}%)"
    
    def _score_buy_sell_ratio(self, buys: int, sells: int) -> Tuple[float, str]:
        """Score buy/sell ratio (0-15 points)"""
        if sells == 0 and buys > 0:
            return 15, f"🔥 All buys, no sells ({buys} buys) (+15)"
        elif buys == 0:
            return 0, f"⚠️ No buy activity"
        
        ratio = buys / max(sells, 1)
        
        if ratio >= self.thresholds['txns_ratio']['bullish']:
            return 15, f"🔥 Bullish ratio ({buys} buys / {sells} sells) (+15)"
        elif ratio >= self.thresholds['txns_ratio']['neutral']:
            return 10, f"📊 Balanced trading ({buys} buys / {sells} sells) (+10)"
        else:
            return 5, f"⚠️ More sells than buys ({buys} buys / {sells} sells) (+5)"
    
    def _score_signal_type(self, signal_type: str) -> Tuple[float, str]:
        """Score based on signal type (0-10 points)"""
        if signal_type == 'graduating':
            return 10, "⚡ Early entry (graduating soon) (+10)"
        elif signal_type == 'graduated':
            return 5, "✅ Graduated token (+5)"
        else:
            return 0, ""
    
    def _score_market_cap(self, market_cap: float) -> Tuple[float, str]:
        """Score market cap for graduating tokens (0-10 points)"""
        # For graduating tokens, lower MC = more upside potential
        if market_cap < 30000:
            return 10, f"💎 Low MC (${market_cap:,.0f}) - high upside (+10)"
        elif market_cap < 50000:
            return 5, f"💎 Good MC (${market_cap:,.0f}) (+5)"
        else:
            return 0, ""

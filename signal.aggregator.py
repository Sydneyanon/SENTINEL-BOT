"""
Signal Aggregator - Combines signals from multiple sources
Tracks signal overlap and calculates aggregate confidence
"""
import asyncio
from typing import Dict, List, Set
from datetime import datetime, timedelta
from loguru import logger


class SignalAggregator:
    """
    Aggregates signals from multiple sources and identifies high-confidence overlaps
    """
    
    def __init__(self, bonding_monitor):
        self.bonding_monitor = bonding_monitor
        
        # Store signals by token
        self.signals: Dict[str, Dict] = {}
        # Format: token_mint -> {
        #   'smart_money': [wallet_data, ...],
        #   'kols': [wallet_data, ...],
        #   'telegram': [call_data, ...],
        #   'twitter': [call_data, ...],
        #   'first_seen': datetime,
        #   'last_updated': datetime,
        #   'signal_count': int
        # }
    
    async def add_smart_money_signal(self, token_mint: str, wallet_data: dict):
        """Add a smart money buy signal"""
        if token_mint not in self.signals:
            self.signals[token_mint] = {
                'smart_money': [],
                'kols': [],
                'telegram': [],
                'twitter': [],
                'first_seen': datetime.now(),
                'last_updated': datetime.now(),
                'signal_count': 0
            }
        
        # Determine if this is smart money or KOL based on tier
        tier = wallet_data.get('tier', 'high')
        if tier in ['elite', 'high']:
            self.signals[token_mint]['smart_money'].append(wallet_data)
        else:
            self.signals[token_mint]['kols'].append(wallet_data)
        
        self.signals[token_mint]['last_updated'] = datetime.now()
        self._update_signal_count(token_mint)
    
    async def add_telegram_signal(self, token_mint: str, call_data: dict):
        """Add a Telegram call signal"""
        if token_mint not in self.signals:
            self.signals[token_mint] = {
                'smart_money': [],
                'kols': [],
                'telegram': [],
                'twitter': [],
                'first_seen': datetime.now(),
                'last_updated': datetime.now(),
                'signal_count': 0
            }
        
        self.signals[token_mint]['telegram'].append(call_data)
        self.signals[token_mint]['last_updated'] = datetime.now()
        self._update_signal_count(token_mint)
    
    async def add_twitter_signal(self, token_mint: str, call_data: dict):
        """Add a Twitter call signal"""
        if token_mint not in self.signals:
            self.signals[token_mint] = {
                'smart_money': [],
                'kols': [],
                'telegram': [],
                'twitter': [],
                'first_seen': datetime.now(),
                'last_updated': datetime.now(),
                'signal_count': 0
            }
        
        self.signals[token_mint]['twitter'].append(call_data)
        self.signals[token_mint]['last_updated'] = datetime.now()
        self._update_signal_count(token_mint)
    
    def _update_signal_count(self, token_mint: str):
        """Update the count of unique signal sources"""
        signals = self.signals[token_mint]
        count = 0
        
        if signals['smart_money']:
            count += 1
        if signals['kols']:
            count += 1
        if signals['telegram']:
            count += 1
        if signals['twitter']:
            count += 1
        
        signals['signal_count'] = count
    
    async def get_hot_tokens(self, min_signals: int = 2, max_age_seconds: int = 300) -> Dict[str, Dict]:
        """
        Get tokens with multiple signal sources (high confidence)
        
        Args:
            min_signals: Minimum number of different signal sources required
            max_age_seconds: Maximum age of signals to consider
        
        Returns:
            Dict of token_mint -> signal_data for tokens meeting criteria
        """
        hot_tokens = {}
        cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
        
        for token_mint, data in list(self.signals.items()):
            # Check if signals are fresh enough
            if data['last_updated'] < cutoff:
                continue
            
            # Check if meets minimum signal threshold
            if data['signal_count'] >= min_signals:
                hot_tokens[token_mint] = data
        
        return hot_tokens
    
    async def cleanup_old_signals(self, max_age_hours: int = 24):
        """Remove signals older than max_age_hours"""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        removed = 0
        for token_mint in list(self.signals.keys()):
            if self.signals[token_mint]['last_updated'] < cutoff:
                del self.signals[token_mint]
                removed += 1
        
        if removed > 0:
            logger.debug(f"Cleaned up {removed} old signals")

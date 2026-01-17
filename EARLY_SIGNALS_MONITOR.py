"""
Early Signals Monitor - Main coordinator for pre-graduation detection
Combines smart money, KOL, and social signals for 0-70% curve entries
"""
import asyncio
import os
from datetime import datetime, timedelta
from typing import Dict, Set
from loguru import logger
from dotenv import load_dotenv

from smart_money_tracker import SmartMoneyTracker
from tg_calls_monitor import TelegramCallsMonitor
from x_calls_monitor import TwitterCallsMonitor
from pump_bonding_monitor import PumpBondingMonitor
from signal_aggregator import SignalAggregator

load_dotenv()

# Configuration
SIGNAL_AGGREGATION_WINDOW = 300  # 5 minutes to aggregate signals
MIN_EARLY_CONVICTION = 85  # Minimum score to post early signal


class EarlySignalsMonitor:
    """
    Coordinates all early detection sources and aggregates signals
    Only posts ultra-high conviction early entries to avoid spam
    """
    
    def __init__(self, db, telegram_publisher, conviction_filter):
        self.db = db
        self.telegram = telegram_publisher
        self.conviction_filter = conviction_filter
        
        # Initialize monitors
        self.smart_money = SmartMoneyTracker()
        self.tg_calls = TelegramCallsMonitor()
        self.x_calls = TwitterCallsMonitor()
        self.bonding_monitor = PumpBondingMonitor()
        self.aggregator = SignalAggregator(self.bonding_monitor)
        
        # Track processed tokens
        self.processed_tokens: Set[str] = set()
        self.signal_callback = None
        
        self.running = False
    
    def set_signal_callback(self, callback):
        """Set callback for when high-conviction early signal is detected"""
        self.signal_callback = callback
    
    async def start(self):
        """Start all monitoring systems"""
        self.running = True
        
        logger.info("🎯 Early Signals Monitor starting...")
        
        # Set up callbacks from each monitor
        self.smart_money.set_callback(self._on_smart_money_buy)
        self.tg_calls.set_callback(self._on_telegram_call)
        self.x_calls.set_callback(self._on_twitter_call)
        
        # Start all monitors
        await asyncio.gather(
            self.smart_money.start(),
            self.tg_calls.start(),
            self.x_calls.start(),
            self._aggregation_loop()
        )
    
    async def stop(self):
        """Stop all monitors"""
        self.running = False
        await self.smart_money.stop()
        await self.tg_calls.stop()
        await self.x_calls.stop()
        logger.info("Early signals monitor stopped")
    
    async def _on_smart_money_buy(self, token_mint: str, wallet_data: dict):
        """Called when smart money wallet buys a token"""
        logger.info(f"💰 Smart money buy: {wallet_data['name']} bought {token_mint[:8]}")
        await self.aggregator.add_smart_money_signal(token_mint, wallet_data)
    
    async def _on_telegram_call(self, token_mint: str, call_data: dict):
        """Called when Telegram call is detected"""
        logger.info(f"📱 Telegram call: {call_data['caller']} called {token_mint[:8]}")
        await self.aggregator.add_telegram_signal(token_mint, call_data)
    
    async def _on_twitter_call(self, token_mint: str, call_data: dict):
        """Called when Twitter call is detected"""
        logger.info(f"🐦 Twitter call: @{call_data['username']} called {token_mint[:8]}")
        await self.aggregator.add_twitter_signal(token_mint, call_data)
    
    async def _aggregation_loop(self):
        """Periodically check aggregated signals and evaluate for posting"""
        while self.running:
            try:
                # Get tokens with multiple signals
                hot_tokens = await self.aggregator.get_hot_tokens(
                    min_signals=2,  # Need at least 2 different signal sources
                    max_age_seconds=SIGNAL_AGGREGATION_WINDOW
                )
                
                for token_mint, signal_data in hot_tokens.items():
                    # Skip if already processed
                    if token_mint in self.processed_tokens:
                        continue
                    
                    # Skip if already in database (already signaled)
                    if await self.db.has_seen(token_mint):
                        continue
                    
                    # Evaluate this token
                    await self._evaluate_early_token(token_mint, signal_data)
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Error in aggregation loop: {e}", exc_info=True)
                await asyncio.sleep(30)
    
    async def _evaluate_early_token(self, token_mint: str, signal_data: dict):
        """
        Evaluate if aggregated signals meet threshold for early signal
        """
        try:
            logger.info(f"🔍 EVALUATING EARLY TOKEN: {token_mint[:8]}")
            logger.info(f"   Signals: {signal_data['signal_count']} sources")
            
            # Get bonding curve progress
            curve_data = await self.bonding_monitor.get_curve_progress(token_mint)
            
            if not curve_data:
                logger.debug(f"Could not get curve data for {token_mint[:8]}")
                return
            
            curve_pct = curve_data['completion_percent']
            
            # Only signal if in early range (0-70%)
            if curve_pct > 70:
                logger.info(f"❌ {token_mint[:8]} at {curve_pct:.1f}% curve - too late")
                self.processed_tokens.add(token_mint)
                return
            
            logger.info(f"✅ {token_mint[:8]} at {curve_pct:.1f}% curve - in range!")
            
            # Calculate early conviction score
            early_score = await self._calculate_early_conviction(
                token_mint,
                signal_data,
                curve_data
            )
            
            logger.info(f"📊 Early conviction score: {early_score}")
            
            # Only post if ultra-high conviction
            if early_score >= MIN_EARLY_CONVICTION:
                logger.success(f"🚀 ULTRA EARLY SIGNAL: {token_mint[:8]} scored {early_score}!")
                
                # Mark as processed
                self.processed_tokens.add(token_mint)
                
                # Trigger callback to post signal
                if self.signal_callback:
                    await self.signal_callback(
                        token_mint,
                        signal_type="ultra_early",
                        early_score=early_score,
                        curve_completion=curve_pct,
                        signal_data=signal_data
                    )
            else:
                logger.info(f"📉 Score {early_score} below threshold {MIN_EARLY_CONVICTION}")
        
        except Exception as e:
            logger.error(f"Error evaluating early token: {e}", exc_info=True)
    
    async def _calculate_early_conviction(
        self,
        token_mint: str,
        signal_data: dict,
        curve_data: dict
    ) -> float:
        """
        Calculate conviction score for early-stage token
        Much more aggressive scoring than post-graduation
        """
        score = 0
        reasons = []
        
        # Base score from curve position (earlier = better)
        curve_pct = curve_data['completion_percent']
        if curve_pct < 20:
            score += 40
            reasons.append("Ultra early (0-20% curve)")
        elif curve_pct < 40:
            score += 30
            reasons.append("Very early (20-40% curve)")
        elif curve_pct < 60:
            score += 20
            reasons.append("Early (40-60% curve)")
        else:
            score += 10
            reasons.append("Pre-graduation (60-70% curve)")
        
        # Smart money signals (HUGE weight)
        smart_money_count = len(signal_data.get('smart_money', []))
        if smart_money_count >= 3:
            score += 50
            reasons.append(f"🔥 {smart_money_count} alpha wallets buying")
        elif smart_money_count >= 2:
            score += 40
            reasons.append(f"⚡ {smart_money_count} alpha wallets buying")
        elif smart_money_count >= 1:
            score += 30
            reasons.append(f"💰 {smart_money_count} alpha wallet buying")
        
        # KOL/caller signals
        kol_count = len(signal_data.get('kols', []))
        if kol_count >= 2:
            score += 35
            reasons.append(f"📢 {kol_count} KOLs calling")
        elif kol_count >= 1:
            score += 25
            reasons.append(f"📢 {kol_count} KOL calling")
        
        # Social signals (Telegram + Twitter)
        tg_calls = len(signal_data.get('telegram', []))
        x_calls = len(signal_data.get('twitter', []))
        social_total = tg_calls + x_calls
        
        if social_total >= 3:
            score += 30
            reasons.append(f"🌐 {social_total} social calls")
        elif social_total >= 2:
            score += 20
            reasons.append(f"🌐 {social_total} social calls")
        elif social_total >= 1:
            score += 10
            reasons.append(f"🌐 {social_total} social call")
        
        # Signal overlap bonus (multiple independent sources)
        if signal_data['signal_count'] >= 4:
            score += 25
            reasons.append("🎯 Strong signal convergence")
        elif signal_data['signal_count'] >= 3:
            score += 15
            reasons.append("🎯 Multiple signal sources")
        
        # Volume velocity (if available from curve data)
        if curve_data.get('volume_24h', 0) > 10000:
            score += 15
            reasons.append(f"💹 Strong volume: ${curve_data['volume_24h']:,.0f}")
        
        # Holder growth
        holder_count = curve_data.get('holder_count', 0)
        if holder_count > 100:
            score += 10
            reasons.append(f"👥 {holder_count} holders")
        elif holder_count > 50:
            score += 5
            reasons.append(f"👥 {holder_count} holders")
        
        logger.info(f"Early conviction breakdown: {', '.join(reasons)}")
        
        return score

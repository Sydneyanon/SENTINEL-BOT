"""
Sentinel Signals - Main Bot Entry Point
Monitors pump.fun graduations + Helius webhooks + KOL wallets + EARLY DETECTION
"""
import os
import sys
import asyncio
import signal
from datetime import datetime, timedelta
from loguru import logger
from dotenv import load_dotenv
from aiohttp import web

# Load environment variables
load_dotenv()

# Import all components
from database import Database
from telegram_publisher import TelegramPublisher
from pumpfun_monitor import PumpfunMonitor
from helius_graduation_monitor import HeliusGraduationMonitor
from kol_wallet_tracker import KOLWalletTracker
from performance_tracker import PerformanceTracker
from momentum_analyzer import MomentumAnalyzer
from outcome_tracker import OutcomeTracker
from telegram_admin_bot import TelegramAdminBot
from conviction_filter import ConvictionFilter

# NEW: Early detection system
from early_signals_monitor import EarlySignalsMonitor

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "sentinel.db")
PORT = int(os.getenv("PORT", 8080))
MIN_CONVICTION_SCORE = float(os.getenv("MIN_CONVICTION_SCORE", 70))
MIN_EARLY_CONVICTION = float(os.getenv("MIN_EARLY_CONVICTION", 85))

# Global shutdown flag
shutdown_event = asyncio.Event()

# Global references
graduation_monitor = None
db = None
publisher = None
kol_tracker = None
early_monitor = None

# Race condition prevention
processing_tokens = set()  # Track tokens currently being processed
processing_lock = asyncio.Lock()

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


async def daily_recap_task(performance_tracker):
    """Run daily recap at midnight"""
    while True:
        now = datetime.now()
        # Calculate seconds until next midnight
        tomorrow = now + timedelta(days=1)
        midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_midnight = (midnight - now).total_seconds()
        
        logger.info(f"Next daily recap in {seconds_until_midnight/3600:.1f} hours")
        await asyncio.sleep(seconds_until_midnight)
        await performance_tracker.post_daily_recap()


async def health_check_server():
    async def health(request):
        return web.Response(text="OK", status=200)
    
    async def helius_webhook(request):
        try:
            data = await request.json()
            logger.info(f"📥 Received Helius webhook: {len(data) if isinstance(data, list) else 1} transaction(s)")
            
            if graduation_monitor:
                await graduation_monitor.process_webhook(data)
                return web.Response(text="OK", status=200)
            else:
                logger.warning("Graduation monitor not initialized")
                return web.Response(text="Monitor not ready", status=503)
                
        except Exception as e:
            logger.error(f"Error processing webhook: {e}", exc_info=True)
            return web.Response(text="Error", status=500)
    
    async def kol_webhook(request):
        """Handle KOL wallet transaction webhooks from Helius"""
        try:
            data = await request.json()
            logger.debug(f"📥 Received KOL transaction webhook")
            
            if kol_tracker:
                await kol_tracker.process_webhook(data)
                return web.Response(text="OK", status=200)
            else:
                logger.warning("KOL tracker not initialized")
                return web.Response(text="Tracker not ready", status=503)
                
        except Exception as e:
            logger.error(f"Error processing KOL webhook: {e}", exc_info=True)
            return web.Response(text="Error", status=500)
    
    async def smart_money_webhook(request):
        """Handle smart money wallet webhooks from Helius (EARLY DETECTION)"""
        try:
            data = await request.json()
            logger.debug(f"📥 Received smart money webhook")
            
            if early_monitor and early_monitor.smart_money:
                await early_monitor.smart_money.process_webhook(data)
                return web.Response(text="OK", status=200)
            else:
                logger.warning("Smart money tracker not initialized")
                return web.Response(text="Monitor not ready", status=503)
                
        except Exception as e:
            logger.error(f"Error processing smart money webhook: {e}", exc_info=True)
            return web.Response(text="Error", status=500)
    
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    app.router.add_post("/webhook/graduation", helius_webhook)
    app.router.add_post("/webhook/kol-transaction", kol_webhook)
    app.router.add_post("/webhook/smart-money", smart_money_webhook)  # NEW!
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    logger.info(f"✓ Health check server started on port {PORT}")
    logger.info(f"✓ Webhook endpoint: POST /webhook/graduation")
    logger.info(f"✓ Webhook endpoint: POST /webhook/kol-transaction")
    logger.info(f"✓ Webhook endpoint: POST /webhook/smart-money (EARLY DETECTION)")


async def post_kol_followup(token_mint: str, symbol: str, kol_boost: float, kol_reasons: list):
    """Post a follow-up when additional KOLs buy an already-called token"""
    try:
        message = f"""🎯 **KOL ALERT: ${symbol}**

{chr(10).join(kol_reasons)}

Score boost: +{kol_boost:.0f}

[View Chart](https://dexscreener.com/solana/{token_mint})
""".strip()
        
        await publisher.send_message(message)
        logger.info(f"✓ Posted KOL follow-up for {symbol} (+{kol_boost})")
    
    except Exception as e:
        logger.error(f"Error posting KOL follow-up: {e}", exc_info=True)


async def main():
    global graduation_monitor, db, publisher, kol_tracker, early_monitor
    
    logger.info("=" * 60)
    logger.info("SENTINEL SIGNALS - Starting up...")
    logger.info("=" * 60)
    
    try:
        db = Database()
        await db.initialize()
        logger.info("✓ Database ready")
        
        publisher = TelegramPublisher()
        logger.info("✓ Telegram publisher ready")
        
        conviction_filter = ConvictionFilter()
        logger.info(f"✓ Conviction filter ready (min score: {MIN_CONVICTION_SCORE})")
        
        pumpfun = PumpfunMonitor()
        logger.info("✓ PumpFun monitor ready (WebSocket)")
        
        graduation_monitor = HeliusGraduationMonitor()
        logger.info("✓ Helius graduation monitor ready (Webhook)")
        
        kol_tracker = KOLWalletTracker()
        
        # Set callback for when KOLs buy already-called tokens
        async def on_kol_buy_existing(token_mint: str, kol_name: str, kol_count: int):
            """Called when a KOL buys a token we already posted"""
            signal = await db.get_signal(token_mint)
            if signal and signal['posted']:
                symbol = signal['symbol']
                kol_boost, kol_reasons = kol_tracker.get_kol_buy_boost(token_mint)
                await post_kol_followup(token_mint, symbol, kol_boost, kol_reasons)
        
        # Set callback for early signal detection (KOL buys)
        async def on_kol_early_buy(token_mint: str, kol_name: str):
            """Called when a KOL buys a NEW token - check if we should signal early"""
            existing = await db.has_seen(token_mint)
            if existing:
                logger.debug(f"Token {token_mint[:8]} already signaled, skipping early check")
                return
            
            logger.info(f"🔍 KOL EARLY CHECK: {kol_name} bought {token_mint[:8]}... evaluating")
            await process_token(token_mint, signal_type="kol_early", kol_name=kol_name)
        
        kol_tracker.set_existing_token_callback(on_kol_buy_existing)
        kol_tracker.set_early_signal_callback(on_kol_early_buy)
        logger.info("✓ KOL wallet tracker ready (with early signal detection)")
        
        # NEW: Initialize early detection system
        early_monitor = EarlySignalsMonitor(db, publisher, conviction_filter)
        
        # Set callback for ultra-early signals
        async def on_ultra_early_signal(
            token_mint: str,
            signal_type: str,
            early_score: float,
            curve_completion: float,
            signal_data: dict
        ):
            """Called when ultra-early high-conviction signal is detected"""
            logger.success(f"🎯 ULTRA EARLY SIGNAL DETECTED!")
            logger.info(f"   Token: {token_mint[:8]}")
            logger.info(f"   Curve: {curve_completion:.1f}%")
            logger.info(f"   Score: {early_score}")
            logger.info(f"   Sources: {signal_data.get('signal_count', 0)}")
            
            # Process with special ultra_early signal type
            await process_token(
                token_mint,
                signal_type="ultra_early",
                early_score=early_score,
                curve_completion=curve_completion,
                signal_data=signal_data
            )
        
        early_monitor.set_signal_callback(on_ultra_early_signal)
        logger.info(f"✓ Early detection system ready (min score: {MIN_EARLY_CONVICTION})")
        
        performance_tracker = PerformanceTracker(db, publisher)
        momentum_analyzer = MomentumAnalyzer(db, publisher)
        outcome_tracker = OutcomeTracker(db)
        logger.info("✓ Performance tracker ready")
        logger.info("✓ Momentum analyzer ready")
        logger.info("✓ Outcome tracker ready")
        
        admin_bot = None
        if ADMIN_BOT_TOKEN:
            admin_bot = TelegramAdminBot(db, outcome_tracker)
            await admin_bot.start()
            logger.info("✓ Admin bot ready")
        
        await health_check_server()
        
        async def process_token(
            token_mint: str,
            signal_type: str = "graduated",
            kol_name: str = None,
            curve_completion: float = None,
            early_score: float = None,
            signal_data: dict = None
        ):
            """
            Process a token - deduplicated with lock to prevent race conditions
            
            signal_type can be:
            - "ultra_early" (0-70% curve, multiple signal sources)
            - "kol_early" (KOL bought)
            - "graduated" (100% curve, on Raydium)
            """
            
            # Prevent duplicate processing from multiple sources
            async with processing_lock:
                if token_mint in processing_tokens:
                    logger.debug(f"Token {token_mint} already being processed, skipping")
                    return
                
                # Check database INSIDE the lock to prevent race condition
                existing = await db.get_signal(token_mint)
                if existing:
                    logger.debug(f"Token {token_mint} already in database, skipping")
                    return
                
                processing_tokens.add(token_mint)
            
            try:
                if signal_type == "ultra_early":
                    logger.info(f"🎯 PROCESSING ULTRA EARLY: {token_mint} at {curve_completion:.1f}% curve")
                elif signal_type == "kol_early":
                    logger.info(f"🎯 KOL EARLY SIGNAL CHECK: {token_mint} (triggered by {kol_name})")
                else:
                    logger.info(f"🔍 Processing token: {token_mint}")
                
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            logger.warning(f"DexScreener returned {resp.status} for {token_mint}")
                            return
                        
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        
                        if not pairs:
                            logger.info(f"❌ No DEX data for {token_mint} (might be too early or not liquid yet)")
                            return
                        
                        pair = pairs[0]
                        
                        token_data = {
                            "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0)),
                            "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
                            "price_change_24h": float(pair.get("priceChange", {}).get("h24", 0)),
                            "txns_24h_buys": int(pair.get("txns", {}).get("h24", {}).get("buys", 0)),
                            "txns_24h_sells": int(pair.get("txns", {}).get("h24", {}).get("sells", 0)),
                            "market_cap": float(pair.get("marketCap", 0)),
                            "signal_type": signal_type
                        }
                        
                        # For ultra_early signals, we already have the score
                        if signal_type == "ultra_early":
                            final_score = early_score
                            reasons = []
                            
                            # Build reasons from signal_data
                            smart_money = signal_data.get('smart_money', [])
                            kols = signal_data.get('kols', [])
                            telegram = signal_data.get('telegram', [])
                            twitter = signal_data.get('twitter', [])
                            
                            if smart_money:
                                names = ', '.join([w['name'] for w in smart_money[:3]])
                                reasons.append(f"💰 {len(smart_money)} alpha wallets: {names}")
                            if kols:
                                names = ', '.join([w['name'] for w in kols[:3]])
                                reasons.append(f"📢 {len(kols)} KOLs: {names}")
                            if telegram:
                                reasons.append(f"📱 {len(telegram)} Telegram calls")
                            if twitter:
                                reasons.append(f"🐦 {len(twitter)} Twitter calls")
                            
                            reasons.append(f"⚡ {curve_completion:.1f}% bonding curve")
                        else:
                            # Calculate score normally
                            score, reasons = conviction_filter.calculate_conviction_score(token_data)
                            
                            # Check KOL involvement
                            kol_boost = 0
                            try:
                                if hasattr(kol_tracker, 'get_kol_buy_boost'):
                                    kol_boost, kol_reasons = kol_tracker.get_kol_buy_boost(token_mint)
                                    if kol_boost > 0:
                                        reasons.extend(kol_reasons)
                                        logger.info(f"🎯 KOL boost: +{kol_boost} ({', '.join(kol_reasons)})")
                            except Exception as e:
                                logger.debug(f"KOL boost check failed: {e}")
                            
                            final_score = score + kol_boost
                            
                            # Different thresholds for different signal types
                            if signal_type == "kol_early":
                                min_score = MIN_CONVICTION_SCORE - 10  # Lenient (60 if base is 70)
                            else:
                                min_score = MIN_CONVICTION_SCORE  # Standard (70)
                            
                            if final_score < min_score:
                                logger.info(f"📉 {pair.get('baseToken', {}).get('symbol', 'UNKNOWN')} scored {final_score:.0f} (below {min_score})")
                                return
                        
                        conviction_data = {
                            "symbol": pair.get("baseToken", {}).get("symbol", "UNKNOWN"),
                            "name": pair.get("baseToken", {}).get("name", ""),
                            "address": token_mint,
                            "conviction_score": final_score,
                            "conviction_reasons": reasons,
                            "priceUsd": float(pair.get("priceUsd", 0)),
                            "liquidity_usd": token_data["liquidity_usd"],
                            "volume_24h": token_data["volume_24h"],
                            "price_change_24h": token_data["price_change_24h"],
                            "market_cap": token_data["market_cap"],
                            "pair_address": pair.get("pairAddress", ""),
                            "dex_url": pair.get("url", ""),
                            "signal_type": signal_type,
                            "kol_name": kol_name,
                            "curve_completion": curve_completion,
                            "signal_sources": signal_data
                        }
                        
                        if signal_type == "ultra_early":
                            logger.success(f"🚀 ULTRA EARLY SIGNAL: {conviction_data['symbol']} scored {final_score:.0f}! ({curve_completion:.1f}% curve)")
                        elif signal_type == "kol_early":
                            logger.success(f"🚀 KOL EARLY SIGNAL: {conviction_data['symbol']} scored {final_score:.0f}! (triggered by {kol_name})")
                        else:
                            logger.info(f"🚀 {conviction_data['symbol']} scored {final_score:.0f}!")
                        
                        message_id = await publisher.post_signal(conviction_data)
                        
                        if message_id:
                            await db.add_signal(
                                token_mint,
                                conviction_data["symbol"],
                                conviction_data.get("name", ""),
                                final_score,
                                conviction_data["priceUsd"],
                                conviction_data["liquidity_usd"],
                                conviction_data["volume_24h"],
                                conviction_data["pair_address"],
                                message_id
                            )
                            
                            await performance_tracker.track_token(token_mint)
                            logger.info(f"✅ Signal posted and tracking started for {conviction_data['symbol']}")
                    
            except Exception as e:
                logger.error(f"Error processing {token_mint}: {e}", exc_info=True)
            
            finally:
                # Always remove from processing set
                processing_tokens.discard(token_mint)
        
        graduation_monitor.set_callback(process_token)
        
        logger.info("=" * 60)
        logger.info("🚀 ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 60)
        logger.info("📱 Send /help to your admin bot for commands")
        logger.info("🔍 Monitoring for signals...")
        logger.info(f"🎯 Ultra-early signals: {MIN_EARLY_CONVICTION}+ (0-70% curve)")
        logger.info(f"🎯 KOL early signal score: {MIN_CONVICTION_SCORE - 10}")
        logger.info(f"🎯 Graduation signal score: {MIN_CONVICTION_SCORE}")
        logger.info("🎓 Helius webhooks configured:")
        logger.info("   • Graduation: /webhook/graduation")
        logger.info("   • KOL wallets: /webhook/kol-transaction")
        logger.info("   • Smart money: /webhook/smart-money (EARLY DETECTION)")
        logger.info("")
        
        # Start bonding monitor
        await early_monitor.bonding_monitor.start()
        
        tasks = [
            asyncio.create_task(pumpfun.start(process_token)),
            asyncio.create_task(kol_tracker.start()),
            asyncio.create_task(early_monitor.start()),  # NEW: Early detection system
            asyncio.create_task(performance_tracker.start()),
            asyncio.create_task(momentum_analyzer.start()),
            asyncio.create_task(outcome_tracker.start()),
            asyncio.create_task(daily_recap_task(performance_tracker)),
        ]
        
        await shutdown_event.wait()
        
        logger.info("Shutting down gracefully...")
        for task in tasks:
            task.cancel()
        
        await pumpfun.stop()
        await kol_tracker.stop()
        await early_monitor.stop()  # NEW
        await performance_tracker.stop()
        await momentum_analyzer.stop()
        await outcome_tracker.stop()
        if admin_bot:
            await admin_bot.stop()
        await db.close()
        
        logger.info("✅ Shutdown complete")
    
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)

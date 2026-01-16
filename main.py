"""
Sentinel Signals - Main Bot Entry Point
Monitors pump.fun graduations + graduating tokens + KOL wallets
"""

import os
import sys
import asyncio
import signal
from loguru import logger
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

from database import Database
from telegram_publisher import TelegramPublisher
from pumpfun_monitor import PumpfunMonitor
from graduating_monitor import GraduatingMonitor
from kol_wallet_tracker import KOLWalletTracker
from performance_tracker import PerformanceTracker
from momentum_analyzer import MomentumAnalyzer
from outcome_tracker import OutcomeTracker
from telegram_admin_bot import TelegramAdminBot
from conviction_filter import ConvictionFilter

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
ADMIN_BOT_TOKEN = os.getenv('ADMIN_BOT_TOKEN')
DB_PATH = os.getenv('DB_PATH', 'sentinel.db')
PORT = int(os.getenv('PORT', 8080))
MIN_CONVICTION_SCORE = float(os.getenv('MIN_CONVICTION_SCORE', 70))

shutdown_event = asyncio.Event()


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


async def health_check_server():
    async def health(request):
        return web.Response(text="OK", status=200)
    
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/', health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"✓ Health check server started on port {PORT}")


async def main():
    logger.info("=" * 60)
    logger.info("SENTINEL SIGNALS - Starting up...")
    logger.info("=" * 60)
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("❌ Missing required env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID")
        sys.exit(1)
    
    try:
        db = Database(DB_PATH)
        await db.initialize()
        logger.info("✓ Database ready")
        
        telegram_publisher = TelegramPublisher()
        logger.info("✓ Telegram publisher ready")
        
        pumpfun = PumpfunMonitor()
        logger.info("✓ PumpFun monitor ready (WebSocket)")
        
        graduating = GraduatingMonitor()
        logger.info("✓ Graduating monitor ready (85%+ curve)")
        
        kol_tracker = KOLWalletTracker()
        logger.info("✓ KOL wallet tracker ready")
        
        performance_tracker = PerformanceTracker(db, telegram_publisher)
        logger.info("✓ Performance tracker ready")
        
        momentum_analyzer = MomentumAnalyzer(db, telegram_publisher)
        logger.info("✓ Momentum analyzer ready")
        
        outcome_tracker = OutcomeTracker(db)
        logger.info("✓ Outcome tracker ready")
        
        admin_bot = None
        if ADMIN_BOT_TOKEN:
            admin_bot = TelegramAdminBot(db, outcome_tracker)
            logger.info("✓ Admin bot ready")
        else:
            logger.warning("⚠️ Admin bot disabled (no ADMIN_BOT_TOKEN)")
        
        conviction_filter = ConvictionFilter()
        logger.info("✓ Conviction filter ready")
        
        await health_check_server()
        
        logger.info("=" * 60)
        logger.info("🚀 ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 60)
        if admin_bot:
            logger.info("📱 Send /help to your admin bot for commands")
        logger.info("🔍 Monitoring for high-conviction signals...")
        logger.info(f"🎯 Min conviction score: {MIN_CONVICTION_SCORE}")
        
        async def process_token(token_data: dict):
            try:
                address = token_data.get('address', '')
                symbol = token_data.get('symbol', 'UNKNOWN')
                signal_type = token_data.get('signal_type', 'graduated')
                
                if await db.has_seen(address):
                    logger.debug(f"Skipping {symbol} - already posted")
                    return
                
                kol_boost, kol_reasons = kol_tracker.get_kol_buy_boost(address)
                
                base_score, base_reasons = conviction_filter.calculate_conviction_score(token_data)
                
                total_score = min(base_score + kol_boost, 100)
                all_reasons = base_reasons + kol_reasons
                
                logger.info(f"📊 {symbol} scored {total_score}/100 (base: {base_score}, KOL: +{kol_boost})")
                
                if total_score >= MIN_CONVICTION_SCORE:
                    token_data['conviction_score'] = total_score
                    token_data['conviction_reasons'] = all_reasons
                    
                    signal_emoji = {
                        'graduated': '🚀',
                        'graduating': '⚡',
                    }.get(signal_type, '🔔')
                    
                    token_data['signal_emoji'] = signal_emoji
                    
                    await telegram_publisher.post_signal(token_data)
                    
                    await db.save_signal(token_data, posted=True)
                    
                    logger.info(f"✅ Posted signal: {symbol} ({total_score}/100)")
                else:
                    await db.save_signal(token_data, posted=False)
                    
                    logger.debug(f"⏭️ Skipped {symbol} - score too low ({total_score} < {MIN_CONVICTION_SCORE})")
            
            except Exception as e:
                logger.error(f"Error processing token {token_data.get('symbol', 'UNKNOWN')}: {e}", exc_info=True)
        
        tasks = []
        
        tasks.append(asyncio.create_task(pumpfun.start(process_token)))
        tasks.append(asyncio.create_task(graduating.start(process_token)))
        tasks.append(asyncio.create_task(kol_tracker.start()))
        
        tasks.append(asyncio.create_task(performance_tracker.start()))
        tasks.append(asyncio.create_task(momentum_analyzer.start()))
        tasks.append(asyncio.create_task(outcome_tracker.start()))
        
        if admin_bot:
            tasks.append(asyncio.create_task(admin_bot.start()))
        
        await shutdown_event.wait()
        
        logger.info("🛑 Shutting down gracefully...")
        
        for task in tasks:
            task.cancel()
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        await pumpfun.stop()
        await graduating.stop()
        await kol_tracker.stop()
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

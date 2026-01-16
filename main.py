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

# Load environment variables
load_dotenv()

# Import all components
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

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
ADMIN_BOT_TOKEN = os.getenv('ADMIN_BOT_TOKEN')
DB_PATH = os.getenv('DB_PATH', 'sentinel.db')
PORT = int(os.getenv('PORT', 8080))
MIN_CONVICTION_SCORE = float(os.getenv('MIN_CONVICTION_SCORE', 70))

# Global shutdown flag
shutdown_event = asyncio.Event()


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()


# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


async def health_check_server():
    """Simple health check endpoint for Railway"""
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
    """Main bot entry point"""
    
    logger.info("=" * 60)
    logger.info("SENTINEL SIGNALS - Starting up...")
    logger.info("=" * 60)
    
    # Validate required environment variables
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("❌ Missing required env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID")
        sys.exit(1)
    
    try:
        # Initialize database
        db = Database(DB_PATH)
        await db.initialize()
        logger.info("✓ Database ready")
        
        # Initialize Telegram publisher
        telegram_publisher = TelegramPublisher()
        logger.info("✓ Telegram publisher ready")
        
        # Initialize monitors
        pumpfun = PumpfunMonitor()
        logger.info("✓ PumpFun monitor ready (WebSocket)")
        
        graduating = GraduatingMonitor()
        logger.info("✓ Graduating monitor ready (85%+ curve)")
        
        kol_tracker = KOLWalletTracker()
        logger.info("✓ KOL wallet tracker ready")
        
        # Initialize trackers
        performance_tracker = PerformanceTracker(db, telegram_publisher)
        logger.info("✓ Performance tracker ready")
        
        momentum_analyzer = MomentumAnalyzer(db, telegram_publisher)
        logger.info("✓ Momentum analyzer ready")
        
        outcome_tracker = OutcomeTracker(db, telegram_publisher)
        logger.info("✓ Outcome tracker ready")
        
        # Initialize admin bot (optional)
        admin_bot = None
        if ADMIN_BOT_TOKEN:
            admin_bot = TelegramAdminBot(db, outcome_tracker)
            logger.info("✓ Admin bot ready")
        else:
            logger.warning("⚠️ Admin bot disabled (no ADMIN_BOT_TOKEN)")
        
        # Initialize conviction filter
        conviction_filter = ConvictionFilter()
        logger.info("✓ Conviction filter ready")
        
        # Start health check server
        await health_check_server()
        
        logger.info("=" * 60)
        logger.info("🚀 ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 60)
        if admin_bot:
            logger.info("📱 Send /help to your admin bot for commands")
        logger.info("🔍 Monitoring for high-conviction signals...")
        logger.info(f"🎯 Min conviction score: {MIN_CONVICTION_SCORE}")
        
        # Define unified callback for all monitors
        async def process_token(token_data: dict):
            """
            Process a token through conviction filters and post if high-quality
            
            Args:
                token_data: Token information from any monitor
            """
            try:
                address = token_data.get('address', '')
                symbol = token_data.get('symbol', 'UNKNOWN')
                signal_type = token_data.get('signal_type', 'graduated')
                
                # Check if already posted
                if await db.has_seen(address):
                    logger.debug(f"Skipping {symbol} - already posted")
                    return
                
                # Get KOL buy boost
                kol_boost, kol_reasons = kol_tracker.get_kol_buy_boost(address)
                
                # Calculate base conviction score
                base_score, base_reasons = conviction_filter.calculate_conviction_score(token_data)
                
                # Combine scores
                total_score = min(base_score + kol_boost, 100)
                all_reasons = base_reasons + kol_reasons
                
                logger.info(f"📊 {symbol} scored {total_score}/100 (base: {base_score}, KOL: +{kol_boost})")
                
                # Check if passes threshold
                if total_score >= MIN_CONVICTION_SCORE:
                    # Prepare signal
                    token_data['conviction_score'] = total_score
                    token_data['conviction_reasons'] = all_reasons
                    
                    # Add signal type emoji
                    signal_emoji = {
                        'graduated': '🚀',
                        'graduating': '⚡',
                    }.get(signal_type, '🔔')
                    
                    token_data['signal_emoji'] = signal_emoji
                    
                    # Post signal
                    await telegram_publisher.post_signal(token_data)
                    
                    # Mark as seen and add to tracking
                    await db.save_signal(token_data, posted=True)
                    
                    logger.info(f"✅ Posted signal: {symbol} ({total_score}/100)")
                else:
                    # Mark as seen but not posted
                    await db.save_signal(token_data, posted=False)
                    
                    logger.debug(f"⏭️ Skipped {symbol} - score too low ({total_score} < {MIN_CONVICTION_SCORE})")
            
            except Exception as e:
                logger.error(f"Error processing token {token_data.get('symbol', 'UNKNOWN')}: {e}", exc_info=True)
        
        # Create background tasks
        tasks = []
        
        # Monitor tasks
        tasks.append(asyncio.create_task(pumpfun.start(process_token)))
        tasks.append(asyncio.create_task(graduating.start(process_token)))
        tasks.append(asyncio.create_task(kol_tracker.start()))
        
        # Tracker tasks
        tasks.append(asyncio.create_task(performance_tracker.start()))
        tasks.append(asyncio.create_task(momentum_analyzer.start()))
        tasks.append(asyncio.create_task(outcome_tracker.start()))
        
        # Admin bot (if enabled)
        if admin_bot:
            tasks.append(asyncio.create_task(admin_bot.start()))
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
        logger.info("🛑 Shutting down gracefully...")
        
        # Cancel all tasks
        for task in tasks:
            task.cancel()
        
        # Wait for tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Cleanup
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

"""
Sentinel Signals - Main Bot Entry Point
Monitors pump.fun graduations + Helius webhooks + KOL wallets
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
from helius_graduation_monitor import HeliusGraduationMonitor  # NEW: Helius webhook monitor
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

# Global graduation monitor for webhook access
graduation_monitor = None

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


async def health_check_server():
    """Health check + webhook endpoint for Railway and Helius"""
    
    async def health(request):
        return web.Response(text="OK", status=200)
    
    async def helius_webhook(request):
        """Handle Helius graduation webhooks"""
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
    
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/', health)
    app.router.add_post('/webhook/graduation', helius_webhook)  # NEW: Helius webhook endpoint
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"✓ Health check server started on port {PORT}")
    logger.info(f"✓ Webhook endpoint: POST /webhook/graduation")


async def main():
    """Main bot entry point"""
    global graduation_monitor
    
    logger.info("=" * 60)
    logger.info("SENTINEL SIGNALS - Starting up...")
    logger.info("=" * 60)
    
    try:
        # Initialize database
        db = Database(DB_PATH)
        await db.initialize()
        logger.info("✓ Database ready")
        
        # Initialize Telegram publisher
        publisher = TelegramPublisher()
        await publisher.start()
        logger.info("✓ Telegram publisher ready")
        
        # Initialize conviction filter
        conviction_filter = ConvictionFilter(min_score=MIN_CONVICTION_SCORE)
        logger.info(f"✓ Conviction filter ready (min score: {MIN_CONVICTION_SCORE})")
        
        # Initialize monitors
        pumpfun = PumpfunMonitor()
        logger.info("✓ PumpFun monitor ready (WebSocket)")
        
        # Initialize Helius graduation monitor (webhook-based)
        graduation_monitor = HeliusGraduationMonitor()
        logger.info("✓ Helius graduation monitor ready (Webhook)")
        
        # Initialize KOL tracker (optional)
        kol_tracker = KOLWalletTracker()
        logger.info("✓ KOL wallet tracker ready")
        
        # Initialize trackers
        performance_tracker = PerformanceTracker(db, publisher)
        momentum_analyzer = MomentumAnalyzer(db, publisher)
        outcome_tracker = OutcomeTracker(db)
        logger.info("✓ Performance tracker ready")
        logger.info("✓ Momentum analyzer ready")
        logger.info("✓ Outcome tracker ready")
        
        # Initialize admin bot (optional)
        admin_bot = None
        if ADMIN_BOT_TOKEN:
            admin_bot = TelegramAdminBot(db, outcome_tracker)
            await admin_bot.start()
            logger.info("✓ Admin bot ready")
        
        # Start health check server (includes webhook endpoint)
        await health_check_server()
        
        # Token processing callback
        async def process_token(token_mint: str):
            """Process a graduated token through the pipeline"""
            try:
                # Check if already processed
                if await db.has_signal(token_mint):
                    logger.debug(f"Token {token_mint} already processed, skipping")
                    return
                
                logger.info(f"🔍 Processing token: {token_mint}")
                
                # Get conviction score
                conviction_data = await conviction_filter.evaluate_token(token_mint)
                
                if not conviction_data:
                    logger.info(f"❌ No DEX data for {token_mint}")
                    return
                
                # Check KOL involvement
                kol_boost = kol_tracker.get_kol_boost(token_mint)
                final_score = conviction_data['score'] + kol_boost
                
                if kol_boost > 0:
                    logger.info(f"🎯 KOL boost: +{kol_boost} (final: {final_score})")
                
                # Check if meets threshold
                if final_score < MIN_CONVICTION_SCORE:
                    logger.info(f"📉 {conviction_data['symbol']} scored {final_score} (below {MIN_CONVICTION_SCORE})")
                    return
                
                # Post signal
                logger.info(f"🚀 {conviction_data['symbol']} scored {final_score}!")
                message_id = await publisher.post_signal(conviction_data, final_score, kol_boost)
                
                # Track in database
                if message_id:
                    await db.add_signal(
                        token_mint,
                        conviction_data['symbol'],
                        conviction_data.get('name', ''),
                        final_score,
                        conviction_data['price'],
                        conviction_data['liquidity_usd'],
                        conviction_data['volume_24h'],
                        conviction_data['pair_address'],
                        message_id
                    )
                    
                    # Start tracking
                    await performance_tracker.track_token(token_mint)
                    logger.info(f"✅ Tracking started for {conviction_data['symbol']}")
                    
            except Exception as e:
                logger.error(f"Error processing {token_mint}: {e}", exc_info=True)
        
        # Set callback for graduation monitor
        graduation_monitor.set_callback(process_token)
        
        # Start monitoring tasks
        logger.info("=" * 60)
        logger.info("🚀 ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 60)
        logger.info("📱 Send /help to your admin bot for commands")
        logger.info("🔍 Monitoring for high-conviction signals...")
        logger.info(f"🎯 Min conviction score: {MIN_CONVICTION_SCORE}")
        logger.info("🎓 Helius webhook ready - configure at dashboard.helius.dev")
        logger.info("")
        
        tasks = [
            asyncio.create_task(pumpfun.start(process_token)),
            asyncio.create_task(kol_tracker.start(process_token)),
            asyncio.create_task(performance_tracker.start()),
            asyncio.create_task(momentum_analyzer.start()),
            asyncio.create_task(outcome_tracker.start()),
        ]
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
        # Graceful shutdown
        logger.info("Shutting down gracefully...")
        for task in tasks:
            task.cancel()
        
        await pumpfun.stop()
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

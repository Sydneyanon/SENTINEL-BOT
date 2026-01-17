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

load_dotenv()

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

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
ADMIN_BOT_TOKEN = os.getenv('ADMIN_BOT_TOKEN')
DB_PATH = os.getenv('DB_PATH', 'sentinel.db')
PORT = int(os.getenv('PORT', 8080))
MIN_CONVICTION_SCORE = float(os.getenv('MIN_CONVICTION_SCORE', 70))

shutdown_event = asyncio.Event()
graduation_monitor = None

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


async def health_check_server():
    async def health(request):
        return web.Response(text="OK", status=200)
    
    async def helius_webhook(request):
        try:
            data = await request.json()
            logger.info(f"📥 Webhook: {len(data) if isinstance(data, list) else 1} tx")
            
            if graduation_monitor:
                await graduation_monitor.process_webhook(data)
                return web.Response(text="OK", status=200)
            else:
                return web.Response(text="Monitor not ready", status=503)
                
        except Exception as e:
            logger.error(f"Webhook error: {e}", exc_info=True)
            return web.Response(text="Error", status=500)
    
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/', health)
    app.router.add_post('/webhook/graduation', helius_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"✓ Health check server on port {PORT}")
    logger.info(f"✓ Webhook: POST /webhook/graduation")


async def main():
    global graduation_monitor
    
    logger.info("=" * 60)
    logger.info("SENTINEL SIGNALS - Starting up...")
    logger.info("=" * 60)
    
    try:
        db = Database(DB_PATH)
        await db.initialize()
        logger.info("✓ Database ready")
        
        publisher = TelegramPublisher()
        logger.info("✓ Telegram publisher ready")
        
        conviction_filter = ConvictionFilter()
        logger.info(f"✓ Conviction filter (min: {MIN_CONVICTION_SCORE})")
        
        pumpfun = PumpfunMonitor()
        logger.info("✓ PumpFun monitor (WebSocket)")
        
        graduation_monitor = HeliusGraduationMonitor()
        logger.info("✓ Helius monitor (Webhook)")
        
        kol_tracker = KOLWalletTracker()
        logger.info("✓ KOL tracker")
        
        performance_tracker = PerformanceTracker(db, publisher)
        momentum_analyzer = MomentumAnalyzer(db, publisher)
        outcome_tracker = OutcomeTracker(db)
        logger.info("✓ Trackers ready")
        
        admin_bot = None
        if ADMIN_BOT_TOKEN:
            admin_bot = TelegramAdminBot(db, outcome_tracker)
            await admin_bot.start()
            logger.info("✓ Admin bot ready")
        
        await health_check_server()
        
        async def process_token(token_mint: str):
            try:
                # Duplicate check
                try:
                    existing = await db.get_signal(token_mint)
                    if existing:
                        return
                except AttributeError:
                    pass
                
                logger.info(f"🔍 Processing: {token_mint[:8]}...")
                
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            logger.warning(f"DexScreener {resp.status}")
                            return
                        
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        
                        if not pairs:
                            logger.info(f"❌ No pairs")
                            return
                        
                        pair = pairs[0]
                        
                        # SANITY CHECK 1: Valid symbol
                        symbol = pair.get("baseToken", {}).get("symbol", "")
                        if not symbol or symbol == "UNKNOWN":
                            logger.warning(f"❌ Invalid symbol")
                            return
                        
                        # SANITY CHECK 2: Valid price
                        price = float(pair.get("priceUsd", 0))
                        if price == 0:
                            logger.warning(f"❌ {symbol}: No price data")
                            return
                        
                        # SANITY CHECK 3: Minimum liquidity
                        liquidity = float(pair.get("liquidity", {}).get("usd", 0))
                        if liquidity < 5000:
                            logger.info(f"❌ {symbol}: Low liquidity ${liquidity:,.0f}")
                            return
                        
                        # Build token data
                        token_data = {
                            "liquidity_usd": liquidity,
                            "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
                            "price_change_24h": float(pair.get("priceChange", {}).get("h24", 0)),
                            "txns_24h_buys": int(pair.get("txns", {}).get("h24", {}).get("buys", 0)),
                            "txns_24h_sells": int(pair.get("txns", {}).get("h24", {}).get("sells", 0)),
                            "market_cap": float(pair.get("marketCap", 0)),
                            "signal_type": "graduated"
                        }
                        
                        # Calculate score
                        score, reasons = conviction_filter.calculate_conviction_score(token_data)
                        
                        logger.info(f"📊 {symbol}: score={score:.0f}, reasons={len(reasons)}")
                        
                        # SANITY CHECK 4: Minimum base score
                        if score < 30:
                            logger.info(f"📉 {symbol}: Base score too low ({score:.0f})")
                            return
                        
                        # SANITY CHECK 5: Must have reasons
                        if not reasons:
                            logger.warning(f"❌ {symbol}: No scoring reasons!")
                            return
                        
                        # Check KOL boost
                        kol_boost = kol_tracker.get_kol_boost(token_mint)
                        final_score = score + kol_boost
                        
                        if kol_boost > 0:
                            logger.info(f"🎯 KOL boost: +{kol_boost}")
                        
                        # SANITY CHECK 6: Final score threshold
                        if final_score < MIN_CONVICTION_SCORE:
                            logger.info(f"📉 {symbol}: {final_score:.0f} < {MIN_CONVICTION_SCORE}")
                            return
                        
                        logger.info(f"✅ {symbol} PASSED - posting signal!")
                        
                        # Build conviction data
                        conviction_data = {
                            "symbol": symbol,
                            "name": pair.get("baseToken", {}).get("name", ""),
                            "score": final_score,  # Use final_score here!
                            "reasons": reasons,
                            "price": price,
                            "liquidity_usd": liquidity,
                            "volume_24h": token_data["volume_24h"],
                            "price_change_24h": token_data["price_change_24h"],
                            "pair_address": pair.get("pairAddress", ""),
                            "dex_url": pair.get("url", "")
                        }
                        
                        # Post signal
                        message_id = await publisher.post_signal(conviction_data, final_score, kol_boost)
                        
                        if message_id:
                            await db.add_signal(
                                token_mint,
                                symbol,
                                conviction_data.get("name", ""),
                                final_score,
                                price,
                                liquidity,
                                token_data["volume_24h"],
                                conviction_data["pair_address"],
                                message_id
                            )
                            
                            await performance_tracker.track_token(token_mint)
                            logger.info(f"✅ Tracking {symbol}")
                    
            except Exception as e:
                logger.error(f"Error: {e}", exc_info=True)
        
        graduation_monitor.set_callback(process_token)
        
        logger.info("=" * 60)
        logger.info("🚀 ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 60)
        logger.info(f"🎯 Min conviction: {MIN_CONVICTION_SCORE}")
        logger.info("🎓 Helius webhook active")
        logger.info("")
        
        tasks = [
            asyncio.create_task(pumpfun.start(process_token)),
            asyncio.create_task(kol_tracker.start()),
            asyncio.create_task(performance_tracker.start()),
            asyncio.create_task(momentum_analyzer.start()),
            asyncio.create_task(outcome_tracker.start()),
        ]
        
        await shutdown_event.wait()
        
        logger.info("Shutting down...")
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
        logger.critical(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
        sys.exit(0)

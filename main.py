"""
Sentinel Signals - Solana Memecoin Signal Bot
Main entry point
"""

import asyncio
import os
from loguru import logger
from dotenv import load_dotenv

from database import Database
from pumpfun_monitor import PumpfunMonitor
from telegram_publisher import TelegramPublisher
from performance_tracker import PerformanceTracker
from momentum_analyzer import MomentumAnalyzer
from outcome_tracker import OutcomeTracker
from telegram_admin_bot import TelegramAdminBot
from health import start_health_server

load_dotenv()

# Configuration
MIN_CONVICTION_SCORE = int(os.getenv('MIN_CONVICTION_SCORE', 80))

# Configure logger
logger.add("sentinel.log", rotation="1 day", retention="7 days", level="INFO")


from typing import Tuple, List

def calculate_conviction_score(token_data: dict) -> Tuple[int, List[str]]:
    """
    Calculate conviction score for a token
    Returns: (score, reasons)
    """
    score = 0
    reasons = []
    
    liquidity = token_data.get('liquidity_usd', 0)
    volume_24h = token_data.get('volume_24h', 0)
    price_change_24h = token_data.get('price_change_24h', 0)
    txns_buys = token_data.get('txns_24h_buys', 0)
    txns_sells = token_data.get('txns_24h_sells', 0)
    
    # Liquidity scoring
    if 10000 <= liquidity <= 50000:
        score += 20
        reasons.append(f"Optimal liquidity ${liquidity:,.0f}")
    elif liquidity > 50000:
        score += 10
        reasons.append(f"High liquidity ${liquidity:,.0f}")
    
    # Volume scoring
    if volume_24h > 50000:
        score += 15
        reasons.append(f"Strong volume ${volume_24h:,.0f}")
    elif volume_24h > 20000:
        score += 10
        reasons.append(f"Good volume ${volume_24h:,.0f}")
    
    # Price action scoring
    if price_change_24h > 200:
        score += 25
        reasons.append(f"+{price_change_24h:.0f}% MOONING")
    elif price_change_24h > 100:
        score += 20
        reasons.append(f"+{price_change_24h:.0f}% surging")
    elif price_change_24h > 50:
        score += 12
        reasons.append(f"+{price_change_24h:.0f}% rising")
    
    # Buy pressure scoring
    if txns_buys > 0 and txns_sells > 0:
        ratio = txns_buys / max(txns_sells, 1)
        total_txns = txns_buys + txns_sells
        
        if ratio > 2 and total_txns > 100:
            score += 15
            reasons.append(f"Buy pressure {txns_buys}B/{txns_sells}S")
        elif ratio > 1.5 and total_txns > 50:
            score += 8
            reasons.append(f"Positive ratio {ratio:.1f}")
    
    return min(score, 100), reasons


async def main():
    """Main bot entry point"""
    
    logger.info("=" * 60)
    logger.info("SENTINEL SIGNALS - Starting up...")
    logger.info("=" * 60)
    
    # Initialize database
    db = Database()
    await db.initialize()
    logger.info("✓ Database ready")
    
    # Initialize Telegram publisher
    telegram = TelegramPublisher()
    logger.info("✓ Telegram publisher ready")
    
    # Initialize DexScreener monitor
    pumpfun = PumpfunMonitor()
    logger.info("✓ PumpFun monitor ready")
    
    # Initialize performance tracker
    performance_tracker = PerformanceTracker(db, telegram)
    logger.info("✓ Performance tracker ready")
    
    # Initialize momentum analyzer
    momentum_analyzer = MomentumAnalyzer(db, telegram)
    logger.info("✓ Momentum analyzer ready")
    
    # Initialize outcome tracker
    outcome_tracker = OutcomeTracker(db)
    logger.info("✓ Outcome tracker ready")
    
    # Initialize admin bot
    admin_bot = TelegramAdminBot(db, outcome_tracker)
    logger.info("✓ Admin bot ready")
    
    # Define token processing callback
    async def process_token(token_data: dict):
        """Process new token from PumpFun"""
        try:
            # Check if already seen
            if await db.has_seen(token_data['address']):
                return
            
            # Calculate conviction score
            score, reasons = calculate_conviction_score(token_data)
            token_data['conviction_score'] = score
            token_data['conviction_reasons'] = reasons
            
            logger.info(f"Token {token_data.get('symbol', 'UNKNOWN')}: {score} conviction")
            
            # Post if meets threshold
            if score >= MIN_CONVICTION_SCORE:
                await telegram.post_signal(token_data)
                await db.save_signal(token_data, posted=True)
                logger.info(f"✓ Posted {token_data.get('symbol')} to channel")
            else:
                # Mark as seen but not posted
                await db.save_signal(token_data, posted=False)
        
        except Exception as e:
            logger.error(f"Error processing token: {e}", exc_info=True)
    
    # Start all tasks
    tasks = [
        asyncio.create_task(start_health_server()),  # Health check for Railway
        asyncio.create_task(pumpfun.start(process_token)),
        asyncio.create_task(performance_tracker.start()),
        asyncio.create_task(momentum_analyzer.start()),
        asyncio.create_task(outcome_tracker.start()),
        asyncio.create_task(admin_bot.start()),
    ]
    
    logger.info("=" * 60)
    logger.info("🚀 ALL SYSTEMS OPERATIONAL")
    logger.info("=" * 60)
    logger.info("📱 Send /help to your admin bot for commands")
    logger.info("🔍 Monitoring DexScreener for new tokens...")
    
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("⏸️  Shutdown signal received...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Stopping all services...")
        await performance_tracker.stop()
        await momentum_analyzer.stop()
        await outcome_tracker.stop()
        await admin_bot.stop()
        await db.close()
        logger.info("✓ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())

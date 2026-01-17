"""
Telegram Publisher - Posts trading signals to channel
"""
import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv

load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID', '')

class TelegramPublisher:
    """Publishes trading signals to Telegram channel"""
    
    def __init__(self):
        self.bot: Optional[Bot] = None
        self.channel_id = TELEGRAM_CHANNEL_ID
        
    async def initialize(self):
        """Initialize Telegram bot"""
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("⚠️ TELEGRAM_BOT_TOKEN not set - signals won't be posted")
            return False
            
        if not TELEGRAM_CHANNEL_ID:
            logger.warning("⚠️ TELEGRAM_CHANNEL_ID not set - signals won't be posted")
            return False
            
        try:
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
            # Test the bot
            me = await self.bot.get_me()
            logger.info(f"✅ Telegram bot initialized: @{me.username}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize Telegram bot: {e}")
            return False
    
    def _format_signal(self, signal_data: Dict[str, Any]) -> str:
        """Format signal data into Telegram message"""
        
        token_address = signal_data.get('token_address', 'N/A')
        symbol = signal_data.get('symbol', 'UNKNOWN')
        conviction = signal_data.get('conviction_score', 0)
        signal_type = signal_data.get('signal_type', 'standard')
        
        # Get metrics
        metrics = signal_data.get('metrics', {})
        price = metrics.get('price', 0)
        mcap = metrics.get('market_cap', 0)
        liquidity = metrics.get('liquidity', 0)
        volume_24h = metrics.get('volume_24h', 0)
        holders = metrics.get('holders', 0)
        age_minutes = metrics.get('age_minutes', 0)
        
        # Get KOL activity if present
        kol_activity = signal_data.get('kol_activity', {})
        kol_buyers = kol_activity.get('kol_buyers', [])
        
        # Format based on signal type
        if signal_type == 'ultra_early':
            # Ultra-early graduation signal (85%+ bonding curve)
            bonding_progress = signal_data.get('bonding_curve_progress', 0)
            graduation_eta = signal_data.get('graduation_eta_minutes', 0)
            
            message = f"""🚨 **ULTRA EARLY ALERT** 🚨
⚡ Token Approaching Graduation! ⚡

**{symbol}**
📊 Bonding: {bonding_progress:.1f}%
⏰ Est. Graduation: {graduation_eta:.0f} min

**Conviction: {conviction}/100** {"🔥" * (conviction // 20)}

💰 Entry: ${price:.8f}
💧 Liquidity: ${liquidity:,.0f}
📈 Volume 24h: ${volume_24h:,.0f}
👥 Holders: {holders}
⏱️ Age: {age_minutes:.0f}m

🎯 **Why This Signal:**
✅ High bonding curve completion
✅ Strong early momentum
✅ Early entry opportunity

⚠️ **Risk: HIGH** - Pre-graduation timing

🔗 [DexScreener](https://dexscreener.com/solana/{token_address})
🔗 [Pump.fun](https://pump.fun/{token_address})
📋 `{token_address}`

⚡ Trade fast - graduation imminent!"""
        
        else:
            # Standard signal (post-graduation)
            message = f"""🎯 **NEW SIGNAL** 🎯

**{symbol}**
**Conviction: {conviction}/100** {"🔥" * (conviction // 20)}

💰 Price: ${price:.8f}
💎 MCap: ${mcap:,.0f}
💧 Liquidity: ${liquidity:,.0f}
📈 Volume 24h: ${volume_24h:,.0f}
👥 Holders: {holders}
⏱️ Age: {age_minutes:.0f}m

"""
            
            # Add KOL activity if present
            if kol_buyers:
                message += f"👑 **Smart Money Activity:**\n"
                for kol in kol_buyers[:3]:  # Show top 3
                    kol_name = kol.get('name', 'Unknown')
                    kol_tier = kol.get('tier', 'tracked')
                    message += f"   • {kol_name} ({kol_tier})\n"
                message += "\n"
            
            message += f"""🔗 [DexScreener](https://dexscreener.com/solana/{token_address})
🔗 [Birdeye](https://birdeye.so/token/{token_address})
📋 `{token_address}`

⚠️ DYOR - Not financial advice"""
        
        return message
    
    async def post_signal(self, signal_data: Dict[str, Any]) -> bool:
        """Post signal to Telegram channel"""
        
        if not self.bot or not self.channel_id:
            logger.warning("Telegram not configured - skipping post")
            return False
        
        try:
            message = self._format_signal(signal_data)
            
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            
            token_address = signal_data.get('token_address', 'N/A')
            signal_type = signal_data.get('signal_type', 'standard')
            logger.info(f"📤 Posted {signal_type} signal to Telegram: {token_address}")
            return True
            
        except TelegramError as e:
            logger.error(f"❌ Failed to post to Telegram: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error posting to Telegram: {e}")
            return False
    
    async def post_milestone(self, milestone_data: Dict[str, Any]) -> bool:
        """Post milestone update to Telegram channel"""
        
        if not self.bot or not self.channel_id:
            logger.warning("Telegram not configured - skipping milestone post")
            return False
        
        try:
            token_address = milestone_data.get('token_address', 'N/A')
            symbol = milestone_data.get('symbol', 'UNKNOWN')
            milestone_type = milestone_data.get('milestone_type', 'gain')
            current_gain = milestone_data.get('current_gain', 0)
            entry_price = milestone_data.get('entry_price', 0)
            current_price = milestone_data.get('current_price', 0)
            time_elapsed = milestone_data.get('time_elapsed_minutes', 0)
            
            # Format time
            if time_elapsed < 60:
                time_str = f"{time_elapsed:.0f}m"
            else:
                hours = time_elapsed / 60
                time_str = f"{hours:.1f}h"
            
            message = f"""🎉 **MILESTONE HIT** 🎉

**{symbol}** reached **{current_gain:.0f}x**!

📊 Entry: ${entry_price:.8f}
💰 Current: ${current_price:.8f}
📈 Gain: +{(current_gain - 1) * 100:.0f}%
⏱️ Time: {time_str}

🔗 [DexScreener](https://dexscreener.com/solana/{token_address})
📋 `{token_address}`

{"🚀" * min(int(current_gain), 10)} Keep watching!"""
            
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            
            logger.info(f"📤 Posted milestone to Telegram: {symbol} {current_gain}x")
            return True
            
        except TelegramError as e:
            logger.error(f"❌ Failed to post milestone to Telegram: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error posting milestone to Telegram: {e}")
            return False
    
    async def close(self):
        """Close Telegram bot session"""
        # Bot doesn't need explicit closing
        pass

"""
Telegram Publisher - Posts signals to Telegram channel
"""
import os
import asyncio
from aiogram import Bot
from aiogram.enums import ParseMode
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

class TelegramPublisher:
    """Handles posting signals to Telegram channel"""
    
    def __init__(self):
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN not set in environment")
        if not TELEGRAM_CHANNEL_ID:
            raise ValueError("TELEGRAM_CHANNEL_ID not set in environment")
        
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.channel_id = TELEGRAM_CHANNEL_ID
    
    async def post_signal(self, token_data: dict):
        """
        Post a new signal to the channel
        
        Args:
            token_data: Dict with token information
        """
        try:
            message = self._format_signal_message(token_data)
            await self.send_message(message)
            logger.info(f"✓ Posted signal for {token_data.get('symbol', 'UNKNOWN')}")
        except Exception as e:
            logger.error(f"Error posting signal: {e}", exc_info=True)
    
    async def send_message(self, message: str):
        """
        Send a message to the channel
        
        Args:
            message: Message text (supports Markdown)
        """
        try:
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
            raise
    
    def _format_signal_message(self, token_data: dict) -> str:
        """Format a signal into a Telegram message"""
        
        symbol = token_data.get('symbol', 'UNKNOWN')
        name = token_data.get('name', 'Unknown')
        conviction = token_data.get('conviction_score', 0)
        reasons = token_data.get('conviction_reasons', [])
        address = token_data.get('address', '')
        price = token_data.get('priceUsd', 0)
        liquidity = token_data.get('liquidity_usd', 0)
        volume = token_data.get('volume_24h', 0)
        price_change = token_data.get('price_change_24h', 0)
        
        # Build message
        message = f"""
**NEW SIGNAL**
Token: ${symbol}
Conviction: {conviction}/100

**Details:**
Price: ${price:.10f}
Liquidity: ${liquidity:,.0f}
24h Volume: ${volume:,.0f}
24h Change: {price_change:+.1f}%

**Reasons:**
"""
        
        # Add conviction reasons
        for reason in reasons[:5]:  # Max 5 reasons
            message += f"• {reason}\n"
        
        # Add links
        message += f"\n[View on DexScreener](https://dexscreener.com/solana/{address})\n"
        message += f"`{address}`"
        
        return message.strip()

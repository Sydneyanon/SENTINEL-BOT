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
            
        Returns:
            message_id: ID of the posted message
        """
        try:
            message_text = self._format_signal_message(token_data)
            message = await self.send_message(message_text)
            logger.info(f"✓ Posted signal for {token_data.get('symbol', 'UNKNOWN')}")
            return message.message_id
        except Exception as e:
            logger.error(f"Error posting signal: {e}", exc_info=True)
            return None
    
    async def send_message(self, message: str):
        """
        Send a message to the channel
        
        Args:
            message: Message text (supports Markdown)
            
        Returns:
            Message object from Telegram
        """
        try:
            sent_message = await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            return sent_message
        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
            raise
    
    def _format_signal_message(self, token_data: dict) -> str:
        """Format a signal into a clean, professional Telegram message"""
        
        symbol = token_data.get('symbol', 'UNKNOWN')
        name = token_data.get('name', 'Unknown')
        conviction = token_data.get('conviction_score', 0)
        reasons = token_data.get('conviction_reasons', [])
        address = token_data.get('address', '')
        price = token_data.get('priceUsd', 0)
        liquidity = token_data.get('liquidity_usd', 0)
        volume = token_data.get('volume_24h', 0)
        price_change = token_data.get('price_change_24h', 0)
        
        # Determine conviction emoji and label
        if conviction >= 90:
            conviction_emoji = "🔥"
            conviction_label = "VERY HIGH"
        elif conviction >= 80:
            conviction_emoji = "⚡"
            conviction_label = "HIGH"
        elif conviction >= 70:
            conviction_emoji = "✨"
            conviction_label = "STRONG"
        else:
            conviction_emoji = "📊"
            conviction_label = "MODERATE"
        
        # Format market cap if available
        mc = token_data.get('market_cap', 0)
        mc_line = f"💰 Market Cap: ${mc:,.0f}\n" if mc > 0 else ""
        
        # Build clean message with sections
        message = f"""🚨 **SENTINEL SIGNAL** {conviction_emoji}

**${symbol}**
━━━━━━━━━━━━━━━━━━━━━━━

📊 **Conviction Score:** {conviction}/100 ({conviction_label})

💵 **Price:** ${price:.10f}
{mc_line}💧 **Liquidity:** ${liquidity:,.0f}
📈 **24h Volume:** ${volume:,.0f}
{"🔥" if price_change > 0 else "📉"} **24h Change:** {price_change:+.1f}%

📋 **Why This Signal:**
"""
        
        # Add conviction reasons with clean bullets
        for i, reason in enumerate(reasons[:5], 1):
            message += f"  {i}. {reason}\n"
        
        # Add contract address section
        message += f"\n📝 **Contract Address:**\n`{address}`\n"
        
        # Add action buttons
        message += f"\n🔗 [View Chart](https://dexscreener.com/solana/{address})"
        
        return message.strip()

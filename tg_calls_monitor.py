"""
Telegram Calls Monitor - Monitors public caller channels for token calls
Uses Telethon to listen to Telegram channels in real-time
"""
import asyncio
import re
import os
from typing import Optional, Callable, List
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

try:
    from telethon import TelegramClient, events
    from telethon.errors import SessionPasswordNeededError
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    logger.warning("⚠️ Telethon not installed - Telegram monitoring disabled")

load_dotenv()

# Configuration
TG_API_ID = int(os.getenv('TG_API_ID', '0'))
TG_API_HASH = os.getenv('TG_API_HASH', '')
TG_SESSION_NAME = os.getenv('TG_SESSION_NAME', 'sentinel_tg_session')

# Channels to monitor (add your curated list of high-quality caller channels)
MONITORED_CHANNELS = os.getenv('TG_MONITORED_CHANNELS', '').split(',')

# Regex patterns to extract Solana addresses
SOLANA_ADDRESS_PATTERN = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')

# Call keywords
CALL_KEYWORDS = ['call', 'gem', 'entry', 'buy', 'moon', 'pump', 'signal', '🚀', '💎', '🔥']


class TelegramCallsMonitor:
    """Monitors Telegram channels for token calls"""
    
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.callback: Optional[Callable] = None
        self.running = False
        
        # Track recent calls to avoid duplicates
        self.recent_calls = set()
    
    def set_callback(self, callback: Callable):
        """Set callback for when a call is detected"""
        self.callback = callback
    
    async def start(self):
        """Start monitoring Telegram channels"""
        if not TELETHON_AVAILABLE:
            logger.warning("⚠️ Telethon not available - skipping Telegram monitoring")
            return
        
        if not TG_API_ID or not TG_API_HASH:
            logger.warning("⚠️ Telegram API credentials not set - skipping Telegram monitoring")
            return
        
        if not MONITORED_CHANNELS or MONITORED_CHANNELS == ['']:
            logger.warning("⚠️ No Telegram channels configured - skipping Telegram monitoring")
            return
        
        self.running = True
        
        try:
            # Initialize Telegram client
            self.client = TelegramClient(TG_SESSION_NAME, TG_API_ID, TG_API_HASH)
            await self.client.start()
            
            logger.info(f"📱 Telegram monitor started")
            logger.info(f"📱 Monitoring {len(MONITORED_CHANNELS)} channels")
            
            # Register message handler
            @self.client.on(events.NewMessage(chats=MONITORED_CHANNELS))
            async def handle_message(event):
                await self._process_message(event)
            
            logger.success("✅ Telegram monitoring active")
            
            # Keep running
            await self.client.run_until_disconnected()
        
        except Exception as e:
            logger.error(f"Error starting Telegram monitor: {e}", exc_info=True)
    
    async def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.client:
            await self.client.disconnect()
    
    async def _process_message(self, event):
        """Process a new message from a monitored channel"""
        try:
            message_text = event.message.text.lower()
            
            # Check if message contains call keywords
            has_call_keyword = any(keyword in message_text for keyword in CALL_KEYWORDS)
            
            if not has_call_keyword:
                return
            
            # Extract Solana addresses from message
            addresses = SOLANA_ADDRESS_PATTERN.findall(event.message.text)
            
            if not addresses:
                return
            
            # Get channel info
            chat = await event.get_chat()
            channel_name = getattr(chat, 'title', 'Unknown')
            channel_username = getattr(chat, 'username', None)
            
            # Process each address found
            for address in addresses:
                # Basic validation (Solana addresses are typically 32-44 chars)
                if len(address) < 32 or len(address) > 44:
                    continue
                
                # Check if we've seen this recently
                call_key = f"{address}_{channel_username}"
                if call_key in self.recent_calls:
                    continue
                
                self.recent_calls.add(call_key)
                
                # Clean up old entries (keep last 1000)
                if len(self.recent_calls) > 1000:
                    self.recent_calls = set(list(self.recent_calls)[-1000:])
                
                logger.info(f"📱 TG CALL: {channel_name} called {address[:8]}...")
                
                # Trigger callback
                if self.callback:
                    await self.callback(address, {
                        'source': 'telegram',
                        'channel': channel_name,
                        'channel_username': channel_username,
                        'caller': channel_name,
                        'message': event.message.text[:200],  # First 200 chars
                        'timestamp': datetime.now()
                    })
        
        except Exception as e:
            logger.debug(f"Error processing Telegram message: {e}")

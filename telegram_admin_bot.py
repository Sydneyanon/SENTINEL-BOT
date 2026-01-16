"""
Telegram Admin Bot - Commands for checking stats and managing the bot
"""

import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from loguru import logger
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# Your Telegram bot token for admin commands (separate from channel posting)
ADMIN_BOT_TOKEN = os.getenv('ADMIN_BOT_TOKEN')
ADMIN_USER_IDS = [int(uid.strip()) for uid in os.getenv('ADMIN_USER_IDS', '').split(',') if uid.strip()]


class TelegramAdminBot:
    """Admin bot for checking stats and controlling the signal bot"""
    
    def __init__(self, db, outcome_tracker):
        self.db = db
        self.outcome_tracker = outcome_tracker
        self.bot = None
        self.dp = None
        
        if not ADMIN_BOT_TOKEN:
            raise ValueError("ADMIN_BOT_TOKEN not set in environment")
        if not ADMIN_USER_IDS:
            raise ValueError("ADMIN_USER_IDS not set in environment")
    
    def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id in ADMIN_USER_IDS
    
    async def start(self):
        """Start the admin bot"""
        self.bot = Bot(token=ADMIN_BOT_TOKEN)
        self.dp = Dispatcher()
        
        # Register command handlers
        self.dp.message.register(self.cmd_start, Command("start"))
        self.dp.message.register(self.cmd_stats, Command("stats"))
        self.dp.message.register(self.cmd_winrate, Command("winrate"))
        self.dp.message.register(self.cmd_today, Command("today"))
        self.dp.message.register(self.cmd_week, Command("week"))
        self.dp.message.register(self.cmd_month, Command("month"))
        self.dp.message.register(self.cmd_best, Command("best"))
        self.dp.message.register(self.cmd_worst, Command("worst"))
        self.dp.message.register(self.cmd_pending, Command("pending"))
        self.dp.message.register(self.cmd_help, Command("help"))
        
        logger.info("🤖 Admin bot started - send /help for commands")
        
        # Start polling in background
        asyncio.create_task(self.dp.start_polling(self.bot))
    
    async def stop(self):
        """Stop the admin bot"""
        if self.bot:
            await self.bot.session.close()
    
    async def cmd_start(self, message: types.Message):
        """Start command"""
        if not self._is_admin(message.from_user.id):
            await message.reply("⛔ Unauthorized.")
            return
        
        await message.reply(
            "🤖 **Sentinel Signals Admin Bot**\n\n"
            "Welcome! Use /help to see available commands.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_help(self, message: types.Message):
        """Show help"""
        if not self._is_admin(message.from_user.id):
            return
        
        help_text = """
🤖 **Admin Commands**

**Performance Stats:**
/stats - Overall statistics (all-time)
/winrate - Current win rate
/today - Stats for last 24 hours
/week - Stats for last 7 days
/month - Stats for last 30 days

**Signal Info:**
/best - Top 5 best performers
/worst - Top 5 worst performers
/pending - Currently pending signals

**Info:**
/help - Show this message

**Win/Loss Definition:**
- Win = +100% (2x) within 24h
- Loss = -50% or no 2x within 24h
"""
        await message.reply(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_stats(self, message: types.Message):
        """Show overall statistics"""
        if not self._is_admin(message.from_user.id):
            return
        
        await message.reply("⏳ Calculating stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats()
        
        msg = self._format_stats_message(stats, "All-Time")
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_winrate(self, message: types.Message):
        """Show just the win rate"""
        if not self._is_admin(message.from_user.id):
            return
        
        stats = await self.outcome_tracker.get_win_rate_stats()
        
        if stats['total_signals'] == 0:
            await message.reply("📊 No signals evaluated yet.")
            return
        
        msg = f"""
📊 **Win Rate Summary**

Win Rate: **{stats['win_rate']:.1f}%**
Total Signals: {stats['total_signals']}
Wins: {stats['wins']} ✅
Losses: {stats['losses']} ❌
Pending: {stats['pending']} ⏳
"""
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_today(self, message: types.Message):
        """Show stats for last 24 hours"""
        if not self._is_admin(message.from_user.id):
            return
        
        await message.reply("⏳ Calculating 24h stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats(days=1)
        msg = self._format_stats_message(stats, "Last 24 Hours")
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_week(self, message: types.Message):
        """Show stats for last 7 days"""
        if not self._is_admin(message.from_user.id):
            return
        
        await message.reply("⏳ Calculating 7-day stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats(days=7)
        msg = self._format_stats_message(stats, "Last 7 Days")
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_month(self, message: types.Message):
        """Show stats for last 30 days"""
        if not self._is_admin(message.from_user.id):
            return
        
        await message.reply("⏳ Calculating 30-day stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats(days=30)
        msg = self._format_stats_message(stats, "Last 30 Days")
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_best(self, message: types.Message):
        """Show top 5 best performers"""
        if not self._is_admin(message.from_user.id):
            return
        
        outcomes = await self.db.get_outcomes()
        
        if not outcomes:
            await message.reply("📊 No signals evaluated yet.")
            return
        
        # Sort by peak gain
        best = sorted(outcomes, key=lambda x: x['peak_gain'], reverse=True)[:5]
        
        msg = "🏆 **Top 5 Performers**\n\n"
        
        for i, signal in enumerate(best, 1):
            symbol = signal['symbol']
            peak = signal['peak_gain']
            outcome = signal['outcome']
            outcome_gain = signal['outcome_gain']
            
            emoji = "✅" if outcome == "win" else "❌"
            
            msg += f"{i}. ${symbol}\n"
            msg += f"   Peak: {peak:+.1f}%\n"
            msg += f"   Outcome: {outcome_gain:+.1f}% {emoji}\n\n"
        
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_worst(self, message: types.Message):
        """Show top 5 worst performers"""
        if not self._is_admin(message.from_user.id):
            return
        
        outcomes = await self.db.get_outcomes()
        
        if not outcomes:
            await message.reply("📊 No signals evaluated yet.")
            return
        
        # Sort by outcome gain (worst first)
        worst = sorted(outcomes, key=lambda x: x['outcome_gain'])[:5]
        
        msg = "📉 **Top 5 Worst Performers**\n\n"
        
        for i, signal in enumerate(worst, 1):
            symbol = signal['symbol']
            outcome_gain = signal['outcome_gain']
            reason = signal.get('outcome_reason', 'Unknown')
            
            msg += f"{i}. ${symbol}\n"
            msg += f"   Loss: {outcome_gain:.1f}%\n"
            msg += f"   Reason: {reason}\n\n"
        
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_pending(self, message: types.Message):
        """Show currently pending signals"""
        if not self._is_admin(message.from_user.id):
            return
        
        pending = await self.db.get_pending_outcomes()
        
        if not pending:
            await message.reply("📊 No pending signals.")
            return
        
        msg = f"⏳ **{len(pending)} Pending Signals**\n\n"
        
        for signal in pending[:10]:  # Show max 10
            symbol = signal['symbol']
            posted_at = datetime.fromisoformat(signal['posted_at'])
            hours_ago = (datetime.now() - posted_at).total_seconds() / 3600
            
            msg += f"${symbol}\n"
            msg += f"  Posted: {hours_ago:.1f}h ago\n\n"
        
        if len(pending) > 10:
            msg += f"\n...and {len(pending) - 10} more"
        
        await message.reply(msg, parse_mode=ParseMode.MARKDOWN)
    
    def _format_stats_message(self, stats: dict, timeframe: str) -> str:
        """Format statistics into a nice message"""
        
        if stats['total_signals'] == 0:
            return f"📊 **{timeframe}**\n\nNo signals evaluated yet."
        
        msg = f"""
📊 **{timeframe} Performance**

━━━━━━━━━━━━━━━━━━━━━
**Win Rate:** {stats['win_rate']:.1f}%
━━━━━━━━━━━━━━━━━━━━━

**Signals:**
- Total Evaluated: {stats['total_signals']}
- Wins: {stats['wins']} ✅
- Losses: {stats['losses']} ❌
- Pending: {stats['pending']} ⏳

**Performance:**
- Avg Gain (Wins): {stats['avg_gain_on_wins']:+.1f}%
- Avg Loss (Losses): {stats['avg_loss_on_losses']:+.1f}%
"""
        
        if stats['best_performer']:
            best = stats['best_performer']
            msg += f"\n**🏆 Best:** ${best['symbol']} ({best['peak_gain']:+.1f}%)"
        
        if stats['worst_performer']:
            worst = stats['worst_performer']
            msg += f"\n**📉 Worst:** ${worst['symbol']} ({worst['outcome_gain']:+.1f}%)"
        
        msg += "\n\n💡 Win = +100% in 24h | Loss = -50% or timeout"
        
        return msg

"""
Telegram Admin Bot - Commands for checking stats and managing the bot
"""

import asyncio
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
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
        self.app = None
        
        if not ADMIN_BOT_TOKEN:
            raise ValueError("ADMIN_BOT_TOKEN not set in environment")
        if not ADMIN_USER_IDS:
            raise ValueError("ADMIN_USER_IDS not set in environment")
    
    async def start(self):
        """Start the admin bot"""
        self.app = Application.builder().token(ADMIN_BOT_TOKEN).build()
        
        # Register command handlers
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CommandHandler("winrate", self.cmd_winrate))
        self.app.add_handler(CommandHandler("today", self.cmd_today))
        self.app.add_handler(CommandHandler("week", self.cmd_week))
        self.app.add_handler(CommandHandler("month", self.cmd_month))
        self.app.add_handler(CommandHandler("best", self.cmd_best))
        self.app.add_handler(CommandHandler("worst", self.cmd_worst))
        self.app.add_handler(CommandHandler("pending", self.cmd_pending))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        
        logger.info("🤖 Admin bot started - send /help for commands")
        
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
    
    async def stop(self):
        """Stop the admin bot"""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
    
    def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id in ADMIN_USER_IDS
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Unauthorized.")
            return
        
        await update.message.reply_text(
            "🤖 **Sentinel Signals Admin Bot**\n\n"
            "Welcome! Use /help to see available commands.",
            parse_mode='Markdown'
        )
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help"""
        if not self._is_admin(update.effective_user.id):
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
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show overall statistics"""
        if not self._is_admin(update.effective_user.id):
            return
        
        await update.message.reply_text("⏳ Calculating stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats()
        
        message = self._format_stats_message(stats, "All-Time")
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_winrate(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show just the win rate"""
        if not self._is_admin(update.effective_user.id):
            return
        
        stats = await self.outcome_tracker.get_win_rate_stats()
        
        if stats['total_signals'] == 0:
            await update.message.reply_text("📊 No signals evaluated yet.")
            return
        
        message = f"""
📊 **Win Rate Summary**

Win Rate: **{stats['win_rate']:.1f}%**
Total Signals: {stats['total_signals']}
Wins: {stats['wins']} ✅
Losses: {stats['losses']} ❌
Pending: {stats['pending']} ⏳
"""
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show stats for last 24 hours"""
        if not self._is_admin(update.effective_user.id):
            return
        
        await update.message.reply_text("⏳ Calculating 24h stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats(days=1)
        message = self._format_stats_message(stats, "Last 24 Hours")
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show stats for last 7 days"""
        if not self._is_admin(update.effective_user.id):
            return
        
        await update.message.reply_text("⏳ Calculating 7-day stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats(days=7)
        message = self._format_stats_message(stats, "Last 7 Days")
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_month(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show stats for last 30 days"""
        if not self._is_admin(update.effective_user.id):
            return
        
        await update.message.reply_text("⏳ Calculating 30-day stats...")
        
        stats = await self.outcome_tracker.get_win_rate_stats(days=30)
        message = self._format_stats_message(stats, "Last 30 Days")
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_best(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show top 5 best performers"""
        if not self._is_admin(update.effective_user.id):
            return
        
        outcomes = await self.db.get_outcomes()
        
        if not outcomes:
            await update.message.reply_text("📊 No signals evaluated yet.")
            return
        
        # Sort by peak gain
        best = sorted(outcomes, key=lambda x: x['peak_gain'], reverse=True)[:5]
        
        message = "🏆 **Top 5 Performers**\n\n"
        
        for i, signal in enumerate(best, 1):
            symbol = signal['symbol']
            peak = signal['peak_gain']
            outcome = signal['outcome']
            outcome_gain = signal['outcome_gain']
            
            emoji = "✅" if outcome == "win" else "❌"
            
            message += f"{i}. ${symbol}\n"
            message += f"   Peak: {peak:+.1f}%\n"
            message += f"   Outcome: {outcome_gain:+.1f}% {emoji}\n\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_worst(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show top 5 worst performers"""
        if not self._is_admin(update.effective_user.id):
            return
        
        outcomes = await self.db.get_outcomes()
        
        if not outcomes:
            await update.message.reply_text("📊 No signals evaluated yet.")
            return
        
        # Sort by outcome gain (worst first)
        worst = sorted(outcomes, key=lambda x: x['outcome_gain'])[:5]
        
        message = "📉 **Top 5 Worst Performers**\n\n"
        
        for i, signal in enumerate(worst, 1):
            symbol = signal['symbol']
            outcome_gain = signal['outcome_gain']
            reason = signal.get('outcome_reason', 'Unknown')
            
            message += f"{i}. ${symbol}\n"
            message += f"   Loss: {outcome_gain:.1f}%\n"
            message += f"   Reason: {reason}\n\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show currently pending signals"""
        if not self._is_admin(update.effective_user.id):
            return
        
        pending = await self.db.get_pending_outcomes()
        
        if not pending:
            await update.message.reply_text("📊 No pending signals.")
            return
        
        message = f"⏳ **{len(pending)} Pending Signals**\n\n"
        
        for signal in pending[:10]:  # Show max 10
            symbol = signal['symbol']
            posted_at = datetime.fromisoformat(signal['posted_at'])
            hours_ago = (datetime.now() - posted_at).total_seconds() / 3600
            
            message += f"${symbol}\n"
            message += f"  Posted: {hours_ago:.1f}h ago\n\n"
        
        if len(pending) > 10:
            message += f"\n...and {len(pending) - 10} more"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    def _format_stats_message(self, stats: dict, timeframe: str) -> str:
        """Format statistics into a nice message"""
        
        if stats['total_signals'] == 0:
            return f"📊 **{timeframe}**\n\nNo signals evaluated yet."
        
        message = f"""
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
            message += f"\n**🏆 Best:** ${best['symbol']} ({best['peak_gain']:+.1f}%)"
        
        if stats['worst_performer']:
            worst = stats['worst_performer']
            message += f"\n**📉 Worst:** ${worst['symbol']} ({worst['outcome_gain']:+.1f}%)"
        
        message += "\n\n💡 Win = +100% in 24h | Loss = -50% or timeout"
        
        return message

---

## **File: requirements.txt**
```
aiohttp==3.9.1
aiosqlite==0.19.0
python-dotenv==1.0.0
loguru==0.7.2
python-telegram-bot==20.7

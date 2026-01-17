async def _post_milestone_alert(
        self, 
        address: str, 
        symbol: str, 
        milestone: int,
        current_price: float,
        multiplier: float
    ):
        """Post milestone achievement alert to Telegram"""
        try:
            # Emoji and style based on milestone
            if milestone >= 100:
                emoji = "🌙"
                style = "MOON"
            elif milestone >= 50:
                emoji = "💎"
                style = "LEGENDARY"
            elif milestone >= 10:
                emoji = "🔥"
                style = "MASSIVE"
            elif milestone >= 5:
                emoji = "🚀"
                style = "HUGE"
            elif milestone == 2:
                emoji = "🏆"
                style = "WIN CONFIRMED"
            else:
                emoji = "✅"
                style = "HIT"
            
            # Special formatting for 2x WIN
            if milestone == 2:
                message = f"""🎯 **MILESTONE ALERT!** 🏆

**${symbol}** just hit **{milestone}x**

━━━━━━━━━━━━━━━━━━━━━━━

✅ **Status:** WIN CONFIRMED
📊 **Multiplier:** {multiplier:.2f}x
💵 **Current Price:** ${current_price:.10f}

🏆 This signal is now marked as a **WIN** in our tracker!

🔗 [View Chart](https://dexscreener.com/solana/{address})
""".strip()
            else:
                message = f"""🎯 **MILESTONE ALERT!** {emoji}

**${symbol}** just hit **{milestone}x**

━━━━━━━━━━━━━━━━━━━━━━━

🎊 **Performance:** {style}
📊 **Multiplier:** {multiplier:.2f}x
💵 **Current Price:** ${current_price:.10f}

Keep watching this one! 👀

🔗 [View Chart](https://dexscreener.com/solana/{address})
""".strip()
            
            await self.telegram.send_message(message)
            logger.info(f"✓ Posted {milestone}x milestone alert for {symbol}")
        
        except Exception as e:
            logger.error(f"Error posting milestone alert: {e}", exc_info=True)
    
    async def _post_metric_update(
        self,
        address: str,
        symbol: str,
        events: List[Dict],
        current_metrics: Dict
    ):
        """Post an event-driven metric update"""
        try:
            # Determine overall status
            positive_events = ['holder_increase', 'volume_spike', 'liquidity_add', 'price_pump']
            negative_events = ['holder_decrease', 'volume_crash', 'liquidity_pull', 'price_dump']
            
            pos_count = sum(1 for e in events if e['type'] in positive_events)
            neg_count = sum(1 for e in events if e['type'] in negative_events)
            
            if pos_count > neg_count:
                status_emoji = "🔥"
                status = "LOOKING STRONG"
            elif neg_count > pos_count:
                status_emoji = "⚠️"
                status = "CAUTION"
            else:
                status_emoji = "➡️"
                status = "MIXED SIGNALS"
            
            # Build clean event lines
            event_lines = '\n'.join([f"  {e['emoji']} {e['message']}" for e in events])
            
            message = f"""📊 **METRIC ALERT: ${symbol}**

━━━━━━━━━━━━━━━━━━━━━━━

{event_lines}

{status_emoji} **Status:** {status}
💵 **Price:** ${current_metrics['price']:.10f}

🔗 [View Chart](https://dexscreener.com/solana/{address})
""".strip()
            
            await self.telegram.send_message(message)
            logger.info(f"✓ Posted metric update for {symbol} ({len(events)} events)")
        
        except Exception as e:
            logger.error(f"Error posting metric update: {e}", exc_info=True)

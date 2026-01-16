# Sentinel Signals - Solana Memecoin Signal Bot

A complete, professional Telegram bot for tracking Solana memecoin opportunities with automated win rate tracking, milestone alerts, and momentum-based sell signals.

## 🎯 Features

✅ **Token Discovery** - Monitors DexScreener for new tokens every 45 seconds  
✅ **Conviction Scoring** - Multi-factor analysis (liquidity, volume, price action, buy pressure)  
✅ **Milestone Tracking** - Alerts at 2x, 3x, 5x, 10x, 20x, 30x, 40x, 50x, 100x-1000x  
✅ **Momentum Analysis** - Smart sell signals based on price velocity, volume trends, buy/sell pressure  
✅ **Win Rate Tracking** - Automatic outcome evaluation (Win = +100% in 24h, Loss = -50% or timeout)  
✅ **Admin Dashboard** - Telegram bot for checking stats (/stats, /winrate, /today, /week, /month, /best, /worst)  
✅ **Professional Formatting** - Clean, minimal-emoji messages  

## 📁 Project Structure
```
sentinel-signals/
├── main.py                    # Main entry point
├── database.py                # Database handler
├── dexscreener_monitor.py     # Token discovery
├── telegram_publisher.py      # Posts to channel
├── performance_tracker.py     # Milestone alerts
├── momentum_analyzer.py       # Sell signals
├── outcome_tracker.py         # Win/loss tracking
├── telegram_admin_bot.py      # Admin commands
├── requirements.txt           # Dependencies
├── .env                       # Your environment variables
├── .env.example               # Template
├── .gitignore                 # Git ignore file
└── README.md                  # This file
```

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Set Up Telegram Bots

**Channel Bot (for posting signals):**
1. Message @BotFather on Telegram
2. Send `/newbot`
3. Choose name: "Your Signal Bot"
4. Choose username: "your_signal_bot"
5. Copy the token
6. Add bot as admin to your channel

**Admin Bot (for checking stats):**
1. Message @BotFather again
2. Send `/newbot`
3. Choose name: "Your Bot Admin"
4. Choose username: "your_bot_admin_bot"
5. Copy the token
6. Message @userinfobot to get your user ID

### 3. Configure Environment

Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=your_channel_bot_token
TELEGRAM_CHANNEL_ID=@your_channel_username
ADMIN_BOT_TOKEN=your_admin_bot_token
ADMIN_USER_IDS=your_telegram_user_id
```

### 4. Run the Bot
```bash
python main.py
```

You should see:
```
✓ Database ready
✓ Telegram publisher ready
✓ DexScreener monitor ready
✓ Performance tracker ready
✓ Momentum analyzer ready
✓ Outcome tracker ready
✓ Admin bot ready
🚀 ALL SYSTEMS OPERATIONAL
```

### 5. Test Admin Commands

Message your admin bot on Telegram:
- `/help` - See all commands
- `/stats` - View overall performance
- `/winrate` - Check current win rate

## 📊 Admin Commands

Send these to your admin bot on Telegram:
```
/stats     → Overall performance (all-time)
/winrate   → Current win rate percentage
/today     → Last 24 hours stats
/week      → Last 7 days performance
/month     → Last 30 days performance
/best      → Top 5 performers
/worst     → Bottom 5 performers
/pending   → Currently tracking
/help      → Command list
```

Example output:
```
📊 All-Time Performance

Win Rate: 68.3%

Signals:
- Total Evaluated: 127
- Wins: 87 ✅
- Losses: 40 ❌
- Pending: 12 ⏳

Performance:
- Avg Gain (Wins): +185.3%
- Avg Loss (Losses): -42.1%

🏆 Best: $MOON (+2,340%)
📉 Worst: $RUG (-58%)
```

## ⚙️ How It Works

### Signal Flow

1. **Discovery** (every 45s)
   - Polls DexScreener for new Solana tokens
   - Fetches detailed pair data

2. **Scoring** (immediate)
   - Analyzes liquidity, volume, price action, buy pressure
   - Calculates conviction score (0-100)
   - Posts to channel if ≥ 80

3. **Milestone Tracking** (every 30 min)
   - Monitors all posted signals
   - Posts alerts at 2x, 3x, 5x, 10x, 20x... 1000x
   - Stops tracking after 1000x

4. **Momentum Analysis** (every 15 min)
   - Tracks price velocity, acceleration, volume trends
   - Calculates buy/sell pressure ratios
   - Posts STRONG_SELL, PARTIAL_SELL, or HOLD signals

5. **Outcome Evaluation** (every 60 min)
   - Checks if signals hit +100% (WIN) or -50% (LOSS)
   - Evaluates within 24-hour window
   - Tracks peak gains for analysis

## 🎛️ Customization

### Conviction Scoring

Edit `main.py` → `calculate_conviction_score()` to adjust scoring logic.

### Win/Loss Thresholds

Edit `.env`:
```env
WIN_THRESHOLD=100   # Change to 50 for easier wins
LOSS_THRESHOLD=-50  # Change to -30 for tighter stops
EVALUATION_WINDOW_HOURS=24  # Change to 48 for more time
```

### Milestones

Edit `performance_tracker.py`:
```python
MILESTONES = [2, 3, 5, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
# Add or remove milestones as desired
```

### Check Intervals

Edit `.env`:
```env
POLL_INTERVAL=45                    # DexScreener polling
PERFORMANCE_CHECK_INTERVAL_SEC=1800 # Milestone checks (30 min)
MOMENTUM_CHECK_INTERVAL_SEC=900     # Sell signals (15 min)
OUTCOME_CHECK_INTERVAL_SEC=3600     # Win/loss eval (60 min)
```

## 🚂 Deployment (Railway)

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin your-repo-url
git push -u origin main
```

### 2. Deploy to Railway

1. Go to railway.app
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your repository
4. Add environment variables in Railway dashboard
5. Deploy!

### 3. Add Persistence (Railway Volume)

1. In Railway dashboard → Your service → Settings
2. Add volume: `/app/signals.db`
3. Redeploy

## 🔍 Monitoring

Check Railway logs for:
```
✓ Posted DOGE to channel
📊 DOGE: WIN (+145.3%, peak: +230.1%)
✓ Posted 10x milestone alert for DOGE
✓ Posted STRONG_SELL signal for RUG
```

## 📈 Win Rate Explained

**Win Definition:**
- Token hits +100% (2x) within 24 hours

**Loss Definition:**
- Token drops to -50%, OR
- 24 hours pass without hitting +100%

**Why these numbers?**
- +100% (2x): Achievable for memecoins, meaningful gains
- -50% stop: Protects from catastrophic losses
- 24h window: Captures memecoin momentum cycles

**Good win rates:**
- 40-60%: Average
- 60-75%: Good
- 75%+: Excellent

## 🛠️ Troubleshooting

### Bot not posting to channel
- Verify bot is admin in channel
- Check `TELEGRAM_CHANNEL_ID` format (@channel_username)
- Test with: `await telegram.send_message("Test")`

### Admin bot not responding
- Verify you're using correct user ID
- Check `ADMIN_USER_IDS` in `.env`
- Message @userinfobot to confirm your ID

### No tokens found
- DexScreener API might be rate limiting
- Check Railway logs for errors
- Increase `POLL_INTERVAL` to 60+

### Database locked
- Add Railway volume for persistence
- Ensure only one bot instance running

## 📄 License

MIT License - Use freely for personal or commercial projects

## 🙏 Support

For issues, check:
1. Railway logs
2. Environment variables
3. Telegram tokens are valid
4. Test components individually

---

**Built with ❤️ for the Solana memecoin community**

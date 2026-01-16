git add conviction_filter.py
git commit -m "Add conviction filter scoring system"
git push

railway logs -f
```

---

## 📊 **How Scoring Works:**

| Category | Max Points | What It Measures |
|----------|-----------|------------------|
| Liquidity | 25 | $ in pool (safety) |
| Volume | 20 | Trading activity |
| Price Change | 15 | Momentum |
| Buy/Sell Ratio | 15 | Sentiment |
| Signal Type | 10 | Entry timing |
| Market Cap | 10 | Upside potential |
| Vol/Liq Ratio | 5 | Activity level |
| **KOL Boost** | +40 | Smart money |
| **TOTAL** | 100+ | (capped at 100) |

---

**Example Signal:**
```
Token: $PEPE
Base Score: 70
- Excellent liquidity ($52,000) (+25)
- Strong volume ($78,000) (+15)
- Strong pump (+65.2%) (+15)
- Bullish ratio (150 buys / 80 sells) (+15)

KOL Boost: +15
- 2 KOLs bought: CENTED, BRADJAE (+15)

TOTAL: 85/100 POSTED

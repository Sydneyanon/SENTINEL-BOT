"""
KOL Monitor - Uses KOLscan.io API to track influencer mentions
"""

import asyncio
import aiohttp
import os
from typing import Set, Callable, Optional, Dict
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

KOLSCAN_API = os.getenv('KOLSCAN_API_URL', 'https://api.kolscan.io')
KOLSCAN_API_KEY = os.getenv('KOLSCAN_API_KEY', '')
POLL_INTERVAL = int(os.getenv('KOLSCAN_POLL_INTERVAL', 120))  # Check every 2 min
MIN_KOL_TIER = os.getenv('MIN_KOL_TIER', 'all')  # 'top', 'verified', or 'all'


class KOLMonitor:
    """Monitors KOL mentions via KOLscan.io API"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.mentioned_tokens: Dict[str, dict] = {}  # address -> {kols, first_seen, tier}
        self.seen_mention_ids: Set[str] = set()
    
    async def start(self, callback: Optional[Callable] = None):
        """Start monitoring KOLscan"""
        
        if not KOLSCAN_API_KEY:
            logger.warning("⚠️ KOLSCAN_API_KEY not set - KOL monitoring disabled")
            logger.info("📝 Get API key from https://kolscan.io/api")
            return
        
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"📱 KOLscan monitor started")
        logger.info(f"🎯 Tracking {MIN_KOL_TIER} tier KOLs, polling every {POLL_INTERVAL}s")
        
        while self.running:
            try:
                await self._fetch_recent_mentions()
                await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error(f"KOLscan monitor error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def stop(self):
        """Stop the monitor"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _fetch_recent_mentions(self):
        """Fetch recent KOL mentions from KOLscan"""
        try:
            # KOLscan API endpoint for recent mentions
            url = f"{KOLSCAN_API}/v1/mentions/recent"
            
            headers = {
                'Authorization': f'Bearer {KOLSCAN_API_KEY}',
                'Accept': 'application/json'
            }
            
            params = {
                'chain': 'solana',
                'limit': 50,
                'tier': MIN_KOL_TIER if MIN_KOL_TIER != 'all' else None
            }
            
            async with self.session.get(url, headers=headers, params=params, timeout=15) as resp:
                if resp.status == 401:
                    logger.error("❌ KOLscan API key invalid")
                    return
                
                if resp.status == 429:
                    logger.warning("⚠️ KOLscan rate limit hit")
                    return
                
                if resp.status != 200:
                    logger.debug(f"KOLscan returned {resp.status}")
                    return
                
                data = await resp.json()
                mentions = data.get('mentions', data.get('data', []))
                
                if not mentions:
                    logger.debug("No new KOL mentions")
                    return
                
                new_mentions = 0
                
                for mention in mentions:
                    try:
                        mention_id = mention.get('id')
                        
                        # Skip if we've seen this mention
                        if mention_id in self.seen_mention_ids:
                            continue
                        
                        self.seen_mention_ids.add(mention_id)
                        
                        # Limit seen set
                        if len(self.seen_mention_ids) > 5000:
                            self.seen_mention_ids = set(list(self.seen_mention_ids)[-2500:])
                        
                        # Extract token info
                        token_address = mention.get('token_address')
                        kol_name = mention.get('kol_name', 'Unknown KOL')
                        kol_tier = mention.get('kol_tier', 'unknown')
                        kol_winrate = mention.get('kol_winrate', 0)
                        mention_type = mention.get('type', 'mention')  # 'call', 'mention', 'warning'
                        
                        if not token_address:
                            continue
                        
                        # Track this token mention
                        if token_address not in self.mentioned_tokens:
                            self.mentioned_tokens[token_address] = {
                                'kols': [],
                                'first_seen': asyncio.get_event_loop().time(),
                                'highest_tier': kol_tier,
                                'mention_types': []
                            }
                        
                        token_info = self.mentioned_tokens[token_address]
                        token_info['kols'].append({
                            'name': kol_name,
                            'tier': kol_tier,
                            'winrate': kol_winrate,
                            'type': mention_type
                        })
                        token_info['mention_types'].append(mention_type)
                        
                        # Track highest tier
                        tiers = ['top', 'verified', 'tracked', 'unknown']
                        if tiers.index(kol_tier) < tiers.index(token_info['highest_tier']):
                            token_info['highest_tier'] = kol_tier
                        
                        new_mentions += 1
                        
                        logger.info(f"📢 KOL mention: {kol_name} ({kol_tier}, {kol_winrate:.0f}% WR)")
                        logger.info(f"   Token: {token_address[:8]}... | Type: {mention_type}")
                    
                    except Exception as e:
                        logger.debug(f"Error processing mention: {e}")
                
                if new_mentions > 0:
                    logger.info(f"✅ Processed {new_mentions} new KOL mentions")
                
                # Cleanup old mentions (>24h)
                current_time = asyncio.get_event_loop().time()
                to_remove = []
                for addr, info in self.mentioned_tokens.items():
                    if current_time - info['first_seen'] > 86400:  # 24 hours
                        to_remove.append(addr)
                
                for addr in to_remove:
                    del self.mentioned_tokens[addr]
        
        except asyncio.TimeoutError:
            logger.warning("KOLscan API timeout")
        except Exception as e:
            logger.error(f"Error fetching KOL mentions: {e}", exc_info=True)
    
    def get_kol_boost(self, address: str) -> tuple[int, list[str]]:
        """
        Calculate KOL boost score for a token
        
        Returns:
            (boost_score, reasons)
        """
        if address not in self.mentioned_tokens:
            return 0, []
        
        info = self.mentioned_tokens[address]
        kols = info['kols']
        
        if not kols:
            return 0, []
        
        boost = 0
        reasons = []
        
        # Base boost for any KOL mention
        boost += 10
        reasons.append(f"📢 {len(kols)} KOL mention(s) (+10)")
        
        # Tier bonuses
        highest_tier = info['highest_tier']
        if highest_tier == 'top':
            boost += 15
            reasons.append("🔥 Top-tier KOL (+15)")
        elif highest_tier == 'verified':
            boost += 10
            reasons.append("✅ Verified KOL (+10)")
        
        # Multiple KOLs bonus
        if len(kols) >= 3:
            boost += 10
            reasons.append(f"💎 {len(kols)} KOLs calling (+10)")
        
        # High win-rate KOL bonus
        high_winrate_kols = [k for k in kols if k['winrate'] >= 60]
        if high_winrate_kols:
            boost += 5
            avg_wr = sum(k['winrate'] for k in high_winrate_kols) / len(high_winrate_kols)
            reasons.append(f"🎯 High WR KOLs ({avg_wr:.0f}% avg) (+5)")
        
        # Call vs mention
        if 'call' in info['mention_types']:
            boost += 5
            reasons.append("📣 Strong call (not just mention) (+5)")
        
        return min(boost, 30), reasons  # Max +30 from KOLs
    
    def is_mentioned(self, address: str) -> bool:
        """Check if token was mentioned by any KOL"""
        return address in self.mentioned_tokens
    
    def get_kol_info(self, address: str) -> Optional[dict]:
        """Get detailed KOL info for a token"""
        return self.mentioned_tokens.get(address)

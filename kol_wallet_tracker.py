"""
KOL Wallet Tracker - Monitor known influencer wallets via Helius
"""

import asyncio
import aiohttp
import os
from typing import Set, Dict, List, Optional
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

HELIUS_API_KEY = os.getenv('HELIUS_API_KEY', '')
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
POLL_INTERVAL = int(os.getenv('KOL_WALLET_POLL_INTERVAL', 30))  # Check every 30s

# Top Solana memecoin trader wallets (add more as you discover them)
TRACKED_WALLETS = os.getenv('TRACKED_KOL_WALLETS', '').split(',')

# Known top traders (update this list with actual addresses)
DEFAULT_KOL_WALLETS = {
    # Example format - replace with real KOL wallets
    # 'WalletAddress123...': {'name': 'CryptoWhale', 'tier': 'top'},
    # 'WalletAddress456...': {'name': 'SolanaKing', 'tier': 'verified'},
}


class KOLWalletTracker:
    """Tracks wallet activity of known successful traders via Helius"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.kol_positions: Dict[str, Set[str]] = {}  # wallet -> set of token addresses
        self.recent_buys: Dict[str, List[dict]] = {}  # token -> [{wallet, amount, time}]
        self.last_signatures: Dict[str, str] = {}  # wallet -> last processed signature
    
    async def start(self):
        """Start monitoring KOL wallets"""
        
        if not HELIUS_API_KEY:
            logger.warning("⚠️ HELIUS_API_KEY not set - KOL wallet tracking disabled")
            logger.info("📝 Get free API key from https://helius.dev")
            return
        
        # Merge default wallets with env var wallets
        self.tracked_wallets = DEFAULT_KOL_WALLETS.copy()
        for wallet in TRACKED_WALLETS:
            if wallet.strip():
                self.tracked_wallets[wallet.strip()] = {'name': 'Tracked', 'tier': 'tracked'}
        
        if not self.tracked_wallets:
            logger.warning("⚠️ No KOL wallets configured - skipping KOL tracking")
            logger.info("📝 Add wallet addresses to TRACKED_KOL_WALLETS env var")
            return
        
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"👀 KOL wallet tracker started - monitoring {len(self.tracked_wallets)} wallets")
        logger.info(f"🔄 Polling every {POLL_INTERVAL}s via Helius")
        
        while self.running:
            try:
                await self._check_all_wallets()
                await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error(f"KOL tracker error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
    
    async def stop(self):
        """Stop the tracker"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def _check_all_wallets(self):
        """Check all tracked wallets for new transactions"""
        tasks = []
        for wallet_address in self.tracked_wallets.keys():
            tasks.append(self._check_wallet_transactions(wallet_address))
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _check_wallet_transactions(self, wallet_address: str):
        """Check recent transactions for a wallet"""
        try:
            # Get recent signatures
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    wallet_address,
                    {"limit": 10}
                ]
            }
            
            async with self.session.post(HELIUS_RPC, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    logger.debug(f"Helius returned {resp.status} for {wallet_address[:8]}...")
                    return
                
                data = await resp.json()
                signatures = data.get('result', [])
                
                if not signatures:
                    return
                
                # Get the most recent signature
                latest_sig = signatures[0]['signature']
                
                # Skip if we've already processed this
                if self.last_signatures.get(wallet_address) == latest_sig:
                    return
                
                self.last_signatures[wallet_address] = latest_sig
                
                # Parse new transactions
                for sig_info in signatures:
                    if sig_info.get('err'):
                        continue  # Skip failed transactions
                    
                    await self._parse_transaction(wallet_address, sig_info['signature'])
        
        except asyncio.TimeoutError:
            logger.debug(f"Timeout checking wallet {wallet_address[:8]}...")
        except Exception as e:
            logger.debug(f"Error checking wallet {wallet_address[:8]}...: {e}")
    
    async def _parse_transaction(self, wallet_address: str, signature: str):
        """Parse a transaction to detect token buys"""
        try:
            # Get transaction details via Helius Enhanced API
            url = f"https://api.helius.xyz/v0/transactions?api-key={HELIUS_API_KEY}"
            
            payload = {
                "transactions": [signature]
            }
            
            async with self.session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    return
                
                data = await resp.json()
                
                if not data:
                    return
                
                tx = data[0]
                
                # Look for token swaps
                if tx.get('type') == 'SWAP':
                    token_in = tx.get('tokenTransfers', [{}])[0].get('mint')
                    token_out = tx.get('tokenTransfers', [{}])[-1].get('mint')
                    
                    # Check if they bought a new token (not SOL/USDC)
                    if token_out and token_out not in ['So11111111111111111111111111111111111111112', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v']:
                        kol_info = self.tracked_wallets[wallet_address]
                        
                        # Track this buy
                        if token_out not in self.recent_buys:
                            self.recent_buys[token_out] = []
                        
                        self.recent_buys[token_out].append({
                            'wallet': wallet_address,
                            'kol_name': kol_info['name'],
                            'kol_tier': kol_info['tier'],
                            'time': asyncio.get_event_loop().time(),
                            'signature': signature
                        })
                        
                        logger.info(f"🎯 KOL BUY: {kol_info['name']} bought {token_out[:8]}...")
        
        except Exception as e:
            logger.debug(f"Error parsing transaction: {e}")
    
    def get_kol_buy_boost(self, token_address: str) -> tuple[int, list[str]]:
        """
        Calculate boost score based on KOL wallet activity
        
        Returns:
            (boost_score, reasons)
        """
        if token_address not in self.recent_buys:
            return 0, []
        
        buys = self.recent_buys[token_address]
        
        # Filter recent buys (last 30 minutes)
        current_time = asyncio.get_event_loop().time()
        recent = [b for b in buys if current_time - b['time'] < 1800]
        
        if not recent:
            return 0, []
        
        boost = 0
        reasons = []
        
        # Base boost for any KOL buy
        boost += 15
        kol_names = [b['kol_name'] for b in recent]
        reasons.append(f"👀 {len(recent)} KOL(s) bought: {', '.join(kol_names[:3])} (+15)")
        
        # Multiple KOLs buying = strong signal
        if len(recent) >= 2:
            boost += 15
            reasons.append(f"🔥 Multiple KOLs buying ({len(recent)}) (+15)")
        
        # Top-tier KOL bonus
        top_tier = [b for b in recent if b['kol_tier'] == 'top']
        if top_tier:
            boost += 10
            reasons.append(f"💎 Top-tier KOL involved (+10)")
        
        return min(boost, 35), reasons  # Max +35 from KOL buys
    
    def cleanup_old_buys(self):
        """Remove buy records older than 24 hours"""
        current_time = asyncio.get_event_loop().time()
        
        for token in list(self.recent_buys.keys()):
            self.recent_buys[token] = [
                b for b in self.recent_buys[token]
                if current_time - b['time'] < 86400
            ]
            
            if not self.recent_buys[token]:
                del self.recent_buys[token]

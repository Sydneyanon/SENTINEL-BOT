"""
KOL Wallet Tracker - Monitor known influencer wallets via Helius
"""
import asyncio
import aiohttp
import os
from typing import Set, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

HELIUS_API_KEY = os.getenv('HELIUS_API_KEY', '')
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
POLL_INTERVAL = int(os.getenv('KOL_WALLET_POLL_INTERVAL', 30))  # Check every 30s
BUY_EXPIRY_HOURS = int(os.getenv('KOL_BUY_EXPIRY_HOURS', 24))  # Buys are relevant for 24h

# Top 10 KOL wallets from 7-day leaderboard
DEFAULT_KOL_WALLETS = {
    'CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o': {'name': 'CENTED', 'tier': 'top'},
    '8Dg8J8xSeKqtBvL1nBe9waX348w5FSFjVnQaRLMpf7eV': {'name': 'BRADJAE', 'tier': 'top'},
    'Be24Gbf5KisDk1LcWWZsBn8dvB816By7YzYF5zWZnRR6': {'name': 'CHAIRMAN', 'tier': 'top'},
    '4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk': {'name': 'JIJO', 'tier': 'top'},
    '2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f': {'name': 'cupsey', 'tier': 'top'},
    'FAicXNV5FVqtfbpn4Zccs71XcfGeyxBSGbqLDyDJZjke': {'name': 'Radiance', 'tier': 'top'},
    'DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt': {'name': 'The Doc', 'tier': 'top'},
    'GJA1HEbxGnqBhBifH9uQauzXSB53to5rhDrzmKxhSU65': {'name': 'Latuche', 'tier': 'top'},
    'G6fUXjMKPJzCY1rveAE6Qm7wy5U3vZgKDJmN1VPAdiZC': {'name': 'Clukz', 'tier': 'top'},
    '57rXqaQsvgyBKwebP2StfqQeCBjBS4jsrZFJN5aU2V9b': {'name': 'Ram', 'tier': 'top'},
}

# Additional wallets from env var (comma-separated)
TRACKED_WALLETS = os.getenv('TRACKED_KOL_WALLETS', '').split(',')

# Raydium and Jupiter program IDs for swap detection
RAYDIUM_PROGRAMS = {
    '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8',  # Raydium AMM
    'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK',  # Raydium CLMM
}
JUPITER_PROGRAM = 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'


class KOLWalletTracker:
    """Tracks wallet activity of known successful traders via Helius"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.tracked_wallets = {}
        self.recent_buys: Dict[str, List[dict]] = {}  # token -> [{wallet, kol_name, timestamp, amount}]
        self.last_signatures: Dict[str, str] = {}  # wallet -> last processed signature
        self.existing_token_callback = None  # Callback for when KOLs buy already-posted tokens
        self.posted_kol_counts: Dict[str, int] = {}  # token -> number of KOLs we've posted about
    
    def set_existing_token_callback(self, callback):
        """Set callback for when KOLs buy already-called tokens"""
        self.existing_token_callback = callback
    
    async def start(self):
        """Start monitoring KOL wallets"""
        
        if not HELIUS_API_KEY:
            logger.warning("⚠️ HELIUS_API_KEY not set - KOL wallet tracking disabled")
            logger.info("📝 Get free API key from https://helius.dev")
            return
        
        # Merge default wallets with env var wallets
        self.tracked_wallets = DEFAULT_KOL_WALLETS.copy()
        
        # Add any additional wallets from env var
        for wallet in TRACKED_WALLETS:
            wallet = wallet.strip()
            if wallet and wallet not in self.tracked_wallets:
                self.tracked_wallets[wallet] = {'name': 'Tracked', 'tier': 'tracked'}
        
        if not self.tracked_wallets:
            logger.warning("⚠️ No KOL wallets configured")
            return
        
        logger.info(f"📊 Tracking {len(self.tracked_wallets)} KOL wallets:")
        for wallet, info in self.tracked_wallets.items():
            logger.info(f"  • {info['name']} ({info['tier']}): {wallet[:8]}...")
        
        self.session = aiohttp.ClientSession()
        self.running = True
        
        # Start monitoring loop
        asyncio.create_task(self._monitor_loop())
        logger.success("✅ KOL wallet tracking started")
    
    async def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.session:
            await self.session.close()
        logger.info("KOL wallet tracker stopped")
    
    async def _monitor_loop(self):
        """Continuously poll for new transactions"""
        while self.running:
            try:
                for wallet_address, wallet_info in self.tracked_wallets.items():
                    await self._check_wallet_transactions(wallet_address, wallet_info)
                    await asyncio.sleep(2)  # Small delay between wallets
                
                # Clean up old buys
                self._cleanup_old_buys()
                
                await asyncio.sleep(POLL_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in KOL monitoring loop: {e}", exc_info=True)
                await asyncio.sleep(10)
    
    async def _check_wallet_transactions(self, wallet_address: str, wallet_info: dict):
        """Fetch and process recent transactions for a wallet"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    wallet_address,
                    {
                        "limit": 10,
                    }
                ]
            }
            
            # Add 'before' parameter if we have a last signature
            if wallet_address in self.last_signatures:
                payload["params"][1]["before"] = self.last_signatures[wallet_address]
            
            async with self.session.post(HELIUS_RPC, json=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"Helius returned {resp.status} for {wallet_info['name']}")
                    return
                
                data = await resp.json()
                signatures = data.get("result", [])
                
                if not signatures:
                    return
                
                # Update last signature
                self.last_signatures[wallet_address] = signatures[0]["signature"]
                
                # Process each transaction
                for sig_info in reversed(signatures):  # Process oldest first
                    if sig_info.get("err"):
                        continue
                    
                    await self._process_transaction(
                        sig_info["signature"],
                        wallet_address,
                        wallet_info
                    )
        
        except Exception as e:
            logger.debug(f"Error checking {wallet_info['name']}: {e}")
    
    async def _process_transaction(self, signature: str, wallet_address: str, wallet_info: dict):
        """Process a single transaction to detect token buys"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0
                    }
                ]
            }
            
            async with self.session.post(HELIUS_RPC, json=payload) as resp:
                if resp.status != 200:
                    return
                
                data = await resp.json()
                tx = data.get("result")
                
                if not tx or not tx.get("meta"):
                    return
                
                # Check if this is a swap transaction
                if not self._is_swap_transaction(tx):
                    return
                
                # Analyze token balance changes
                bought_tokens = self._analyze_token_changes(tx, wallet_address)
                
                for token_mint, amount in bought_tokens:
                    logger.info(f"🎯 {wallet_info['name']} bought {token_mint[:8]}...")
                    
                    # Initialize list if needed
                    if token_mint not in self.recent_buys:
                        self.recent_buys[token_mint] = []
                    
                    # Check if this wallet already has an entry for this token
                    existing_buy = next(
                        (b for b in self.recent_buys[token_mint] if b['wallet'] == wallet_address), 
                        None
                    )
                    
                    if existing_buy:
                        # Update existing entry - same wallet buying more
                        logger.debug(f"Updating existing buy for {wallet_info['name']} on {token_mint[:8]}...")
                        existing_buy['timestamp'] = datetime.now()
                        existing_buy['amount'] += amount
                        existing_buy['signature'] = signature
                        # Don't trigger callback for same wallet buying more
                    else:
                        # NEW wallet buying this token!
                        previous_unique_kols = len(self.recent_buys[token_mint])
                        
                        self.recent_buys[token_mint].append({
                            'wallet': wallet_address,
                            'kol_name': wallet_info['name'],
                            'tier': wallet_info['tier'],
                            'timestamp': datetime.now(),
                            'amount': amount,
                            'signature': signature
                        })
                        
                        current_unique_kols = len(self.recent_buys[token_mint])
                        
                        # Trigger callback only for NEW wallets on existing tokens
                        if previous_unique_kols > 0 and current_unique_kols > previous_unique_kols:
                            if self.existing_token_callback:
                                # Check if we've already posted about this many unique KOLs
                                last_posted_count = self.posted_kol_counts.get(token_mint, 0)
                                
                                if current_unique_kols > last_posted_count:
                                    asyncio.create_task(
                                        self.existing_token_callback(
                                            token_mint, 
                                            wallet_info['name'],
                                            current_unique_kols
                                        )
                                    )
                                    self.posted_kol_counts[token_mint] = current_unique_kols
        
        except Exception as e:
            logger.debug(f"Error processing transaction: {e}")
    
    def _is_swap_transaction(self, tx: dict) -> bool:
        """Check if transaction involves Raydium or Jupiter"""
        try:
            account_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
            
            for key in account_keys:
                pubkey = key if isinstance(key, str) else key.get("pubkey", "")
                
                if pubkey in RAYDIUM_PROGRAMS or pubkey == JUPITER_PROGRAM:
                    return True
            
            return False
        except:
            return False
    
    def _analyze_token_changes(self, tx: dict, wallet_address: str) -> List[Tuple[str, float]]:
        """Analyze token balance changes to detect buys"""
        bought_tokens = []
        
        try:
            pre_balances = tx.get("meta", {}).get("preTokenBalances", [])
            post_balances = tx.get("meta", {}).get("postTokenBalances", [])
            
            # Create maps by mint address
            pre_map = {}
            for balance in pre_balances:
                if balance.get("owner") == wallet_address:
                    mint = balance.get("mint")
                    amount = float(balance.get("uiTokenAmount", {}).get("uiAmount", 0))
                    pre_map[mint] = amount
            
            post_map = {}
            for balance in post_balances:
                if balance.get("owner") == wallet_address:
                    mint = balance.get("mint")
                    amount = float(balance.get("uiTokenAmount", {}).get("uiAmount", 0))
                    post_map[mint] = amount
            
            # Find tokens with positive balance change (buys)
            for mint in post_map:
                pre_amount = pre_map.get(mint, 0)
                post_amount = post_map.get(mint, 0)
                
                if post_amount > pre_amount:
                    change = post_amount - pre_amount
                    # Filter out tiny amounts (dust)
                    if change > 1:
                        bought_tokens.append((mint, change))
        
        except Exception as e:
            logger.debug(f"Error analyzing token changes: {e}")
        
        return bought_tokens
    
    def _cleanup_old_buys(self):
        """Remove buys older than expiry time"""
        cutoff = datetime.now() - timedelta(hours=BUY_EXPIRY_HOURS)
        
        for token_mint in list(self.recent_buys.keys()):
            self.recent_buys[token_mint] = [
                buy for buy in self.recent_buys[token_mint]
                if buy['timestamp'] > cutoff
            ]
            
            # Remove token entry if no buys remain
            if not self.recent_buys[token_mint]:
                del self.recent_buys[token_mint]
                # Also clean up posted count tracking
                if token_mint in self.posted_kol_counts:
                    del self.posted_kol_counts[token_mint]
    
    def get_kol_buy_boost(self, token_mint: str) -> Tuple[float, List[str]]:
        """
        Calculate conviction score boost based on KOL activity
        Returns: (boost_points, reasons)
        """
        if token_mint not in self.recent_buys:
            return (0, [])
        
        buys = self.recent_buys[token_mint]
        
        if not buys:
            return (0, [])
        
        # Count UNIQUE wallets only (buys list already deduplicated)
        top_kols = [b for b in buys if b['tier'] == 'top']
        tracked_kols = [b for b in buys if b['tier'] == 'tracked']
        
        boost = 0
        reasons = []
        
        # Top-tier KOLs get 15 points each
        if top_kols:
            top_boost = len(top_kols) * 15
            boost += top_boost
            
            # Get unique KOL names (no duplicates)
            kol_names = list(set([b['kol_name'] for b in top_kols]))
            kol_names_str = ', '.join(kol_names[:3])  # Show max 3 names
            
            count_text = f"{len(top_kols)} top KOL" + ("s" if len(top_kols) > 1 else "")
            reasons.append(f"{count_text}: {kol_names_str}")
        
        # Tracked KOLs get 5 points each
        if tracked_kols:
            tracked_boost = len(tracked_kols) * 5
            boost += tracked_boost
            reasons.append(f"{len(tracked_kols)} tracked KOL{'s' if len(tracked_kols) > 1 else ''}")
        
        # Confluence bonus: Multiple unique KOLs on same token
        total_unique_kols = len(buys)
        if total_unique_kols >= 3:
            confluence_boost = 10
            boost += confluence_boost
            reasons.append(f"Strong confluence ({total_unique_kols} KOLs)")
        elif total_unique_kols >= 2:
            confluence_boost = 5
            boost += confluence_boost
            reasons.append(f"Multiple KOLs ({total_unique_kols})")
        
        # Cap total boost at 50 points
        boost = min(boost, 50)
        
        return (boost, reasons)

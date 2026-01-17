        """
KOL Wallet Tracker - Webhook-based monitoring (no polling!)
"""
import os
from typing import Set, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

BUY_EXPIRY_HOURS = int(os.getenv('KOL_BUY_EXPIRY_HOURS', 24))

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

# Raydium and Jupiter program IDs for swap detection
RAYDIUM_PROGRAMS = {
    '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8',
    'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK',
}
JUPITER_PROGRAM = 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'


class KOLWalletTracker:
    """Webhook-based KOL wallet tracker - receives push notifications from Helius"""
    
    def __init__(self):
        self.tracked_wallets = DEFAULT_KOL_WALLETS.copy()
        self.recent_buys: Dict[str, List[dict]] = {}
        self.existing_token_callback = None
        self.posted_kol_counts: Dict[str, int] = {}
        self.running = False
    
    def set_existing_token_callback(self, callback):
        """Set callback for when KOLs buy already-called tokens"""
        self.existing_token_callback = callback
    
    async def start(self):
        """Initialize tracker (webhook-based, no polling loop needed)"""
        logger.info(f"📊 KOL Wallet Tracker initialized (webhook-based)")
        logger.info(f"📊 Tracking {len(self.tracked_wallets)} KOL wallets")
        for wallet, info in self.tracked_wallets.items():
            logger.info(f"  • {info['name']} ({info['tier']}): {wallet[:8]}...")
        
        self.running = True
        logger.success("✅ KOL wallet tracking ready (webhook mode)")
    
    async def stop(self):
        """Stop tracker"""
        self.running = False
        logger.info("KOL wallet tracker stopped")
    
    async def process_webhook(self, webhook_data: list):
        """Process incoming webhook from Helius"""
        try:
            # Helius sends array of transactions
            if not isinstance(webhook_data, list):
                webhook_data = [webhook_data]
            
            for tx_data in webhook_data:
                await self._process_transaction_webhook(tx_data)
        
        except Exception as e:
            logger.error(f"Error processing KOL webhook: {e}", exc_info=True)
    
    async def _process_transaction_webhook(self, tx_data: dict):
        """Process a single transaction from webhook"""
        try:
            # Get account keys to find which wallet this is from
            account_keys = tx_data.get("accountData", [])
            
            # Find which KOL wallet is involved
            kol_wallet = None
            wallet_info = None
            
            for account in account_keys:
                addr = account.get("account", "")
                if addr in self.tracked_wallets:
                    kol_wallet = addr
                    wallet_info = self.tracked_wallets[addr]
                    break
            
            if not kol_wallet:
                return  # Not from our tracked wallets
            
            # Check if this is a swap
            if not self._is_swap_transaction_webhook(tx_data):
                return
            
            # Analyze token changes
            bought_tokens = self._analyze_token_changes_webhook(tx_data, kol_wallet)
            
            for token_mint, amount in bought_tokens:
                logger.info(f"🎯 {wallet_info['name']} bought {token_mint[:8]}...")
                
                # Initialize list if needed
                if token_mint not in self.recent_buys:
                    self.recent_buys[token_mint] = []
                
                # Check if this wallet already has an entry
                existing_buy = next(
                    (b for b in self.recent_buys[token_mint] if b['wallet'] == kol_wallet),
                    None
                )
                
                if existing_buy:
                    # Update existing entry
                    existing_buy['timestamp'] = datetime.now()
                    existing_buy['amount'] += amount
                else:
                    # NEW wallet buying this token
                    previous_unique_kols = len(self.recent_buys[token_mint])
                    
                    self.recent_buys[token_mint].append({
                        'wallet': kol_wallet,
                        'kol_name': wallet_info['name'],
                        'tier': wallet_info['tier'],
                        'timestamp': datetime.now(),
                        'amount': amount
                    })
                    
                    current_unique_kols = len(self.recent_buys[token_mint])
                    
                    # Trigger callback for NEW wallets on existing tokens
                    if previous_unique_kols > 0 and current_unique_kols > previous_unique_kols:
                        if self.existing_token_callback:
                            last_posted_count = self.posted_kol_counts.get(token_mint, 0)
                            
                            if current_unique_kols > last_posted_count:
                                await self.existing_token_callback(
                                    token_mint,
                                    wallet_info['name'],
                                    current_unique_kols
                                )
                                self.posted_kol_counts[token_mint] = current_unique_kols
                
                # Cleanup old buys periodically
                self._cleanup_old_buys()
        
        except Exception as e:
            logger.debug(f"Error processing transaction webhook: {e}")
    
    def _is_swap_transaction_webhook(self, tx_data: dict) -> bool:
        """Check if transaction is a swap"""
        try:
            # Check account keys for Raydium/Jupiter
            account_data = tx_data.get("accountData", [])
            
            for account in account_data:
                addr = account.get("account", "")
                if addr in RAYDIUM_PROGRAMS or addr == JUPITER_PROGRAM:
                    return True
            
            # Also check token transfers for swap indicators
            token_transfers = tx_data.get("tokenTransfers", [])
            return len(token_transfers) >= 2  # Swaps involve at least 2 token transfers
        
        except:
            return False
    
    def _analyze_token_changes_webhook(self, tx_data: dict, wallet_address: str) -> List[Tuple[str, float]]:
        """Analyze token changes from webhook data"""
        bought_tokens = []
        
        try:
            token_transfers = tx_data.get("tokenTransfers", [])
            
            # Track token balance changes for this wallet
            token_changes = {}
            
            for transfer in token_transfers:
                from_addr = transfer.get("fromUserAccount", "")
                to_addr = transfer.get("toUserAccount", "")
                mint = transfer.get("mint", "")
                amount = float(transfer.get("tokenAmount", 0))
                
                # Track incoming tokens to our wallet
                if to_addr == wallet_address:
                    token_changes[mint] = token_changes.get(mint, 0) + amount
                
                # Track outgoing tokens from our wallet
                if from_addr == wallet_address:
                    token_changes[mint] = token_changes.get(mint, 0) - amount
            
            # Find tokens with positive net change (buys)
            for mint, change in token_changes.items():
                if change > 1:  # Filter dust
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
            
            if not self.recent_buys[token_mint]:
                del self.recent_buys[token_mint]
                if token_mint in self.posted_kol_counts:
                    del self.posted_kol_counts[token_mint]
    
    def get_kol_buy_boost(self, token_mint: str) -> Tuple[float, List[str]]:
        """Calculate conviction score boost based on KOL activity"""
        if token_mint not in self.recent_buys:
            return (0, [])
        
        buys = self.recent_buys[token_mint]
        
        if not buys:
            return (0, [])
        
        top_kols = [b for b in buys if b['tier'] == 'top']
        tracked_kols = [b for b in buys if b['tier'] == 'tracked']
        
        boost = 0
        reasons = []
        
        if top_kols:
            top_boost = len(top_kols) * 15
            boost += top_boost
            
            kol_names = list(set([b['kol_name'] for b in top_kols]))
            kol_names_str = ', '.join(kol_names[:3])
            
            count_text = f"{len(top_kols)} top KOL" + ("s" if len(top_kols) > 1 else "")
            reasons.append(f"{count_text}: {kol_names_str}")
        
        if tracked_kols:
            tracked_boost = len(tracked_kols) * 5
            boost += tracked_boost
            reasons.append(f"{len(tracked_kols)} tracked KOL{'s' if len(tracked_kols) > 1 else ''}")
        
        total_unique_kols = len(buys)
        if total_unique_kols >= 3:
            confluence_boost = 10
            boost += confluence_boost
            reasons.append(f"Strong confluence ({total_unique_kols} KOLs)")
        elif total_unique_kols >= 2:
            confluence_boost = 5
            boost += confluence_boost
            reasons.append(f"Multiple KOLs ({total_unique_kols})")
        
        boost = min(boost, 50)
        
        return (boost, reasons)

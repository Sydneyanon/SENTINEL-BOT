"""
Smart Money Tracker - Monitors high-PnL alpha wallets for early buys
Uses Helius webhooks for real-time detection
"""
import asyncio
import aiohttp
import os
from typing import Optional, Callable, Dict, Set
from datetime import datetime, timedelta
from loguru import logger
from dotenv import load_dotenv

from curated_wallets import SMART_MONEY_WALLETS, KOL_WALLETS

load_dotenv()

HELIUS_API_KEY = os.getenv('HELIUS_API_KEY', '')
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Raydium and Jupiter program IDs for swap detection
RAYDIUM_PROGRAMS = {
    '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8',
    'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK',
}
JUPITER_PROGRAM = 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'


class SmartMoneyTracker:
    """
    Tracks buys from curated smart money wallets
    Combines high-PnL snipers + high win-rate KOLs
    """
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.callback: Optional[Callable] = None
        self.running = False
        
        # Combine smart money + KOL wallets
        self.tracked_wallets = {**SMART_MONEY_WALLETS, **KOL_WALLETS}
        
        # Track recent buys (prevent duplicates)
        self.recent_buys: Dict[str, Set[str]] = {}  # token_mint -> set of wallet addresses
    
    def set_callback(self, callback: Callable):
        """Set callback for when smart money buy is detected"""
        self.callback = callback
    
    async def start(self):
        """Start monitoring smart money wallets"""
        self.running = True
        self.session = aiohttp.ClientSession()
        
        logger.info(f"💰 Smart Money Tracker starting...")
        logger.info(f"💰 Tracking {len(self.tracked_wallets)} alpha wallets")
        
        # Log wallet categories
        smart_count = len(SMART_MONEY_WALLETS)
        kol_count = len(KOL_WALLETS)
        logger.info(f"   • {smart_count} high-PnL snipers")
        logger.info(f"   • {kol_count} high win-rate KOLs")
        
        logger.success("✅ Smart money tracking ready (webhook mode)")
    
    async def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.session:
            await self.session.close()
    
    async def process_webhook(self, webhook_data: list):
        """
        Process Helius webhook for smart money wallet transactions
        Should be called from main webhook endpoint
        """
        try:
            if not isinstance(webhook_data, list):
                webhook_data = [webhook_data]
            
            for tx_data in webhook_data:
                await self._process_transaction(tx_data)
        
        except Exception as e:
            logger.error(f"Error processing smart money webhook: {e}", exc_info=True)
    
    async def _process_transaction(self, tx_data: dict):
        """Process a single transaction from webhook"""
        try:
            # Find which wallet this is from
            account_data = tx_data.get("accountData", [])
            
            wallet_address = None
            wallet_info = None
            
            for account in account_data:
                addr = account.get("account", "")
                if addr in self.tracked_wallets:
                    wallet_address = addr
                    wallet_info = self.tracked_wallets[addr]
                    break
            
            if not wallet_address:
                return
            
            # Check if this is a swap
            if not self._is_swap_transaction(tx_data):
                return
            
            # Analyze what token they bought
            bought_tokens = self._analyze_token_purchases(tx_data, wallet_address)
            
            for token_mint, amount in bought_tokens:
                # Check if this is a new buy for this token
                if token_mint not in self.recent_buys:
                    self.recent_buys[token_mint] = set()
                
                if wallet_address in self.recent_buys[token_mint]:
                    continue  # Already saw this wallet buy this token
                
                self.recent_buys[token_mint].add(wallet_address)
                
                logger.info(f"💰 SMART MONEY BUY: {wallet_info['name']} bought {token_mint[:8]}...")
                
                # Trigger callback
                if self.callback:
                    await self.callback(token_mint, {
                        'wallet': wallet_address,
                        'name': wallet_info['name'],
                        'tier': wallet_info['tier'],
                        'win_rate': wallet_info.get('win_rate', 0),
                        'pnl_30d': wallet_info.get('pnl_30d', 0),
                        'amount': amount,
                        'timestamp': datetime.now()
                    })
        
        except Exception as e:
            logger.debug(f"Error processing transaction: {e}")
    
    def _is_swap_transaction(self, tx_data: dict) -> bool:
        """Check if transaction is a swap"""
        try:
            account_data = tx_data.get("accountData", [])
            
            for account in account_data:
                addr = account.get("account", "")
                if addr in RAYDIUM_PROGRAMS or addr == JUPITER_PROGRAM:
                    return True
            
            # Also check for multiple token transfers (swap indicator)
            token_transfers = tx_data.get("tokenTransfers", [])
            return len(token_transfers) >= 2
        
        except:
            return False
    
    def _analyze_token_purchases(self, tx_data: dict, wallet_address: str):
        """Analyze which tokens were bought"""
        bought_tokens = []
        
        try:
            token_transfers = tx_data.get("tokenTransfers", [])
            token_changes = {}
            
            for transfer in token_transfers:
                from_addr = transfer.get("fromUserAccount", "")
                to_addr = transfer.get("toUserAccount", "")
                mint = transfer.get("mint", "")
                amount = float(transfer.get("tokenAmount", 0))
                
                # Track incoming tokens
                if to_addr == wallet_address:
                    token_changes[mint] = token_changes.get(mint, 0) + amount
                
                # Track outgoing tokens
                if from_addr == wallet_address:
                    token_changes[mint] = token_changes.get(mint, 0) - amount
            
            # Find tokens with positive net change (buys)
            for mint, change in token_changes.items():
                if change > 1:  # Filter dust
                    bought_tokens.append((mint, change))
        
        except Exception as e:
            logger.debug(f"Error analyzing purchases: {e}")
        
        return bought_tokens

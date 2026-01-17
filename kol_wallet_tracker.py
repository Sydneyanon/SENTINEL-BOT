"""
KOL Wallet Tracker - Monitor known influencer wallets via Helius webhooks
"""
import asyncio
from typing import Set, Dict, List, Optional
from datetime import datetime, timedelta
from loguru import logger
from curated_wallets import get_all_tracked_wallets

class KOLWalletTracker:
    """Tracks wallet activity of known successful traders via Helius webhooks"""
    
    def __init__(self):
        self.running = False
        self.tracked_wallets = {}
        self.kol_positions: Dict[str, Set[str]] = {}  # wallet -> set of token addresses
        self.recent_buys: Dict[str, List[dict]] = {}  # token -> [{wallet, amount, time}]
        
    async def start(self):
        """Initialize KOL wallet tracking"""
        
        # Load wallets from curated list
        self.tracked_wallets = get_all_tracked_wallets()
        
        if not self.tracked_wallets:
            logger.warning("⚠️ No KOL wallets configured - tracking disabled")
            return
        
        self.running = True
        logger.info(f"✅ KOL Wallet Tracker initialized with {len(self.tracked_wallets)} wallets")
        
        # Log wallet tiers
        elite_count = sum(1 for w in self.tracked_wallets.values() if w.get('tier') == 'elite')
        top_kol_count = sum(1 for w in self.tracked_wallets.values() if w.get('tier') == 'top_kol')
        logger.info(f"   🏆 Elite wallets: {elite_count}")
        logger.info(f"   👑 Top KOLs: {top_kol_count}")
        
        # Start cleanup task
        asyncio.create_task(self._cleanup_old_buys())
    
    async def process_webhook(self, webhook_data: List[Dict]) -> None:
        """
        Process Helius webhook data for KOL transactions
        webhook_data is a list of enhanced transaction objects
        """
        
        if not self.running:
            return
        
        try:
            # Helius sends transactions as a list
            for transaction in webhook_data:
                await self._process_transaction(transaction)
                
        except Exception as e:
            logger.error(f"❌ Error processing KOL webhook: {e}")
    
    async def _process_transaction(self, tx_data: Dict) -> None:
        """Process a single transaction from webhook"""
        
        try:
            # Extract transaction details
            account_data = tx_data.get('accountData', [])
            token_transfers = tx_data.get('tokenTransfers', [])
            native_transfers = tx_data.get('nativeTransfers', [])
            
            # Get the wallet that made the transaction
            fee_payer = tx_data.get('feePayer', '')
            
            # Check if this is a tracked wallet
            if fee_payer not in self.tracked_wallets:
                return
            
            wallet_info = self.tracked_wallets[fee_payer]
            wallet_name = wallet_info.get('name', 'Unknown')
            wallet_tier = wallet_info.get('tier', 'tracked')
            
            # Look for token swaps (buying new tokens)
            for transfer in token_transfers:
                # Check if this is a buy (receiving tokens)
                to_address = transfer.get('toUserAccount', '')
                
                if to_address == fee_payer:
                    token_address = transfer.get('mint', '')
                    amount = transfer.get('tokenAmount', 0)
                    
                    if token_address and amount > 0:
                        # Record the buy
                        await self._record_kol_buy(
                            wallet_address=fee_payer,
                            wallet_name=wallet_name,
                            wallet_tier=wallet_tier,
                            token_address=token_address,
                            amount=amount,
                            timestamp=datetime.utcnow()
                        )
                        
                        logger.info(f"👑 {wallet_name} ({wallet_tier}) bought: {token_address}")
            
        except Exception as e:
            logger.error(f"❌ Error processing transaction: {e}")
    
    async def _record_kol_buy(
        self,
        wallet_address: str,
        wallet_name: str,
        wallet_tier: str,
        token_address: str,
        amount: float,
        timestamp: datetime
    ) -> None:
        """Record a KOL buy for later conviction scoring"""
        
        # Add to positions
        if wallet_address not in self.kol_positions:
            self.kol_positions[wallet_address] = set()
        self.kol_positions[wallet_address].add(token_address)
        
        # Add to recent buys
        if token_address not in self.recent_buys:
            self.recent_buys[token_address] = []
        
        self.recent_buys[token_address].append({
            'wallet': wallet_address,
            'name': wallet_name,
            'tier': wallet_tier,
            'amount': amount,
            'timestamp': timestamp
        })
        
        # Keep only last 50 buys per token
        if len(self.recent_buys[token_address]) > 50:
            self.recent_buys[token_address] = self.recent_buys[token_address][-50:]
    
    def get_kol_activity(self, token_address: str) -> Dict:
        """
        Get KOL activity for a token
        Returns number of KOLs who bought and their details
        """
        
        if token_address not in self.recent_buys:
            return {
                'kol_count': 0,
                'elite_count': 0,
                'kol_buyers': [],
                'boost_multiplier': 1.0
            }
        
        buys = self.recent_buys[token_address]
        
        # Count unique KOLs and tiers
        unique_kols = {}
        for buy in buys:
            wallet = buy['wallet']
            if wallet not in unique_kols:
                unique_kols[wallet] = {
                    'name': buy['name'],
                    'tier': buy['tier'],
                    'first_buy': buy['timestamp']
                }
        
        # Count by tier
        elite_count = sum(1 for k in unique_kols.values() if k['tier'] == 'elite')
        top_kol_count = sum(1 for k in unique_kols.values() if k['tier'] == 'top_kol')
        
        # Calculate boost multiplier
        boost = 1.0
        boost += elite_count * 0.3  # +30% per elite wallet
        boost += top_kol_count * 0.2  # +20% per top KOL
        
        return {
            'kol_count': len(unique_kols),
            'elite_count': elite_count,
            'top_kol_count': top_kol_count,
            'kol_buyers': [
                {
                    'name': info['name'],
                    'tier': info['tier'],
                    'time_ago': (datetime.utcnow() - info['first_buy']).total_seconds() / 60
                }
                for info in unique_kols.values()
            ],
            'boost_multiplier': boost
        }
    
    async def _cleanup_old_buys(self):
        """Clean up buy records older than 24 hours"""
        
        while self.running:
            try:
                await asyncio.sleep(3600)  # Run every hour
                
                cutoff = datetime.utcnow() - timedelta(hours=24)
                
                for token_address in list(self.recent_buys.keys()):
                    # Filter out old buys
                    self.recent_buys[token_address] = [
                        buy for buy in self.recent_buys[token_address]
                        if buy['timestamp'] > cutoff
                    ]
                    
                    # Remove token if no recent buys
                    if not self.recent_buys[token_address]:
                        del self.recent_buys[token_address]
                
                logger.debug("🧹 Cleaned up old KOL buy records")
                
            except Exception as e:
                logger.error(f"❌ Error in KOL cleanup: {e}")
    
    async def stop(self):
        """Stop the tracker"""
        self.running = False
        logger.info("🛑 KOL Wallet Tracker stopped")

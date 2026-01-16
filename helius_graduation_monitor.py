"""
Helius Graduation Monitor - Webhook-based pump.fun graduation detection
"""
import asyncio
from typing import Callable, Optional
from loguru import logger

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class HeliusGraduationMonitor:
    """Monitors pump.fun graduations via Helius webhooks"""
    
    def __init__(self):
        self.callback: Optional[Callable] = None
        self.seen_tokens = set()
        logger.info("🎓 Helius graduation monitor initialized")
    
    def set_callback(self, callback: Callable):
        """Set the callback function for processing graduated tokens"""
        self.callback = callback
        logger.info("✓ Graduation callback registered")
    
    async def process_webhook(self, webhook_data: dict):
        """
        Process incoming Helius webhook data for pump.fun graduations
        
        Webhook data structure:
        [
            {
                "signature": "...",
                "type": "SWAP",
                "accountData": [...],
                "nativeTransfers": [...],
                "tokenTransfers": [...]
            }
        ]
        """
        try:
            if not webhook_data:
                return
            
            # Helius sends an array of transactions
            transactions = webhook_data if isinstance(webhook_data, list) else [webhook_data]
            
            for tx in transactions:
                await self._process_transaction(tx)
                
        except Exception as e:
            logger.error(f"Error processing webhook: {e}", exc_info=True)
    
    async def _process_transaction(self, tx: dict):
        """Process a single transaction to detect graduations"""
        try:
            # Check if this is a pump.fun transaction
            if not self._is_pumpfun_tx(tx):
                return
            
            # Extract token mint from transaction
            token_mint = self._extract_token_mint(tx)
            
            if not token_mint:
                return
            
            # Check if it's a graduation event
            if self._is_graduation(tx):
                # Avoid duplicates
                if token_mint in self.seen_tokens:
                    return
                
                self.seen_tokens.add(token_mint)
                logger.info(f"🎓 GRADUATION DETECTED: {token_mint}")
                
                # Call the callback to process this token
                if self.callback:
                    await self.callback(token_mint)
                    
        except Exception as e:
            logger.error(f"Error processing transaction: {e}", exc_info=True)
    
    def _is_pumpfun_tx(self, tx: dict) -> bool:
        """Check if transaction involves pump.fun program"""
        # Check accountData for pump.fun program
        account_data = tx.get('accountData', [])
        for account in account_data:
            if account.get('account') == PUMP_FUN_PROGRAM:
                return True
        
        # Also check instructions
        instructions = tx.get('instructions', [])
        for instruction in instructions:
            if instruction.get('programId') == PUMP_FUN_PROGRAM:
                return True
        
        return False
    
    def _is_graduation(self, tx: dict) -> bool:
        """
        Detect if transaction is a graduation event
        
        Graduation indicators:
        1. Large swap to Raydium
        2. LP token creation
        3. Specific instruction pattern
        """
        # Check transaction type
        tx_type = tx.get('type', '')
        if tx_type != 'SWAP':
            return False
        
        # Check for Raydium involvement (graduations go to Raydium)
        RAYDIUM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
        
        account_data = tx.get('accountData', [])
        for account in account_data:
            if account.get('account') == RAYDIUM_PROGRAM:
                return True
        
        instructions = tx.get('instructions', [])
        for instruction in instructions:
            if instruction.get('programId') == RAYDIUM_PROGRAM:
                return True
        
        # Check token transfers for large amounts (graduation liquidity)
        token_transfers = tx.get('tokenTransfers', [])
        for transfer in token_transfers:
            # Large token transfers (>1M tokens) often indicate graduation
            amount = transfer.get('tokenAmount', 0)
            if amount > 1_000_000:
                return True
        
        return False
    
    def _extract_token_mint(self, tx: dict) -> Optional[str]:
        """Extract the token mint address from transaction"""
        # Try to get from token transfers
        token_transfers = tx.get('tokenTransfers', [])
        
        if not token_transfers:
            return None
        
        # The token being graduated is usually the one being transferred in large amounts
        # Get the first token transfer's mint address
        for transfer in token_transfers:
            mint = transfer.get('mint')
            if mint and mint != 'So11111111111111111111111111111111111111112':  # Skip SOL
                return mint
        
        return None
    
    def _clean_old_tokens(self):
        """Clean old tokens from seen set to prevent memory bloat"""
        # Keep only last 1000 tokens
        if len(self.seen_tokens) > 1000:
            # Convert to list, keep last 1000, convert back to set
            self.seen_tokens = set(list(self.seen_tokens)[-1000:])

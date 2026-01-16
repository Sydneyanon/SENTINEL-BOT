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


class KOLWalletTracker:
    """Tracks wallet activity of known successful traders via Helius"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.tracked_wallets = {}
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
        
        # Add any additional wallets from env var
        for wallet in TRACKED_WALLETS:
            wallet = wallet.strip()
            if wallet and wallet not in self.tracked_wallets:
                self.tracked_wallets[wallet] = {'name': 'Tracked', 'tier': 'tracked'}

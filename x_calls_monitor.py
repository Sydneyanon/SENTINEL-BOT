"""
Twitter/X Calls Monitor - Monitors Twitter for token calls
Uses twscrape for free Twitter monitoring (requires multiple accounts)
"""
import asyncio
import re
import os
from typing import Optional, Callable
from datetime import datetime, timedelta
from loguru import logger
from dotenv import load_dotenv

try:
    from twscrape import API, gather
    TWSCRAPE_AVAILABLE = True
except ImportError:
    TWSCRAPE_AVAILABLE = False
    logger.warning("⚠️ twscrape not installed - Twitter monitoring disabled")

load_dotenv()

# Configuration
TWITTER_SEARCH_QUERIES = [
    'pump.fun call',
    'solana gem',
    'pump.fun entry',
    '$SOL call'
]

# Regex patterns
SOLANA_ADDRESS_PATTERN = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')

# Minimum follower count to consider
MIN_FOLLOWERS = 500

# Check interval
CHECK_INTERVAL = 60  # Check every 60 seconds


class TwitterCallsMonitor:
    """Monitors Twitter/X for token calls using free search"""
    
    def __init__(self):
        self.api: Optional[API] = None
        self.callback: Optional[Callable] = None
        self.running = False
        
        # Track seen tweets to avoid duplicates
        self.seen_tweets = set()
    
    def set_callback(self, callback: Callable):
        """Set callback for when a call is detected"""
        self.callback = callback
    
    async def start(self):
        """Start monitoring Twitter"""
        if not TWSCRAPE_AVAILABLE:
            logger.warning("⚠️ twscrape not available - skipping Twitter monitoring")
            logger.info("   Install: pip install twscrape")
            logger.info("   Setup: twscrape add_accounts accounts.txt username:password:email:email_password")
            logger.info("   Login: twscrape login_accounts")
            return
        
        self.running = True
        
        try:
            # Initialize API
            self.api = API()
            
            # Check if any accounts are available
            accounts = await self.api.pool.accounts_info()
            if not accounts:
                logger.warning("⚠️ No Twitter accounts configured for twscrape")
                logger.info("   Setup accounts: twscrape add_accounts accounts.txt username:password:email:email_password")
                return
            
            logger.info(f"🐦 Twitter monitor started")
            logger.info(f"🐦 Using {len(accounts)} Twitter accounts")
            logger.success("✅ Twitter monitoring active")
            
            # Start monitoring loop
            while self.running:
                try:
                    await self._search_recent_calls()
                    await asyncio.sleep(CHECK_INTERVAL)
                except Exception as e:
                    logger.error(f"Error in Twitter monitoring loop: {e}")
                    await asyncio.sleep(CHECK_INTERVAL)
        
        except Exception as e:
            logger.error(f"Error starting Twitter monitor: {e}", exc_info=True)
    
    async def stop(self):
        """Stop monitoring"""
        self.running = False
    
    async def _search_recent_calls(self):
        """Search for recent calls on Twitter"""
        try:
            # Search each query
            for query in TWITTER_SEARCH_QUERIES:
                tweets = await gather(self.api.search(query, limit=20))
                
                for tweet in tweets:
                    await self._process_tweet(tweet)
                
                await asyncio.sleep(2)  # Rate limit between queries
        
        except Exception as e:
            logger.debug(f"Error searching Twitter: {e}")
    
    async def _process_tweet(self, tweet):
        """Process a tweet to extract token calls"""
        try:
            # Skip if we've seen this tweet
            if tweet.id in self.seen_tweets:
                return
            
            self.seen_tweets.add(tweet.id)
            
            # Clean up old entries
            if len(self.seen_tweets) > 5000:
                self.seen_tweets = set(list(self.seen_tweets)[-5000:])
            
            # Skip if tweet is too old (only want recent calls)
            tweet_age = datetime.now() - tweet.date
            if tweet_age > timedelta(hours=1):
                return
            
            # Check follower count
            if tweet.user.followersCount < MIN_FOLLOWERS:
                return
            
            # Extract Solana addresses
            addresses = SOLANA_ADDRESS_PATTERN.findall(tweet.rawContent)
            
            if not addresses:
                return
            
            # Process each address
            for address in addresses:
                # Validate address length
                if len(address) < 32 or len(address) > 44:
                    continue
                
                logger.info(f"🐦 TWITTER CALL: @{tweet.user.username} called {address[:8]}...")
                
                # Trigger callback
                if self.callback:
                    await self.callback(address, {
                        'source': 'twitter',
                        'username': tweet.user.username,
                        'caller': f"@{tweet.user.username}",
                        'followers': tweet.user.followersCount,
                        'verified': tweet.user.verified,
                        'tweet_text': tweet.rawContent[:200],
                        'tweet_url': f"https://twitter.com/{tweet.user.username}/status/{tweet.id}",
                        'timestamp': tweet.date
                    })
        
        except Exception as e:
            logger.debug(f"Error processing tweet: {e}")

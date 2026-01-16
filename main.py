"""
Database class for Sentinel Signals
Handles all database operations
"""

import aiosqlite
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger


class Database:
    """SQLite database handler"""
    
    def __init__(self, db_path='signals.db'):
        self.db_path = db_path
        self.conn = None
    
    async def initialize(self):
        """Initialize database and create tables"""
        self.conn = await aiosqlite.connect(self.db_path)
        
        # Create signals table with complete schema
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                address TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                initial_price REAL,
                conviction_score INTEGER,
                posted INTEGER DEFAULT 0,
                posted_at TIMESTAMP,
                posted_milestones TEXT DEFAULT '',
                last_sell_signal TEXT,
                last_sell_signal_at TIMESTAMP,
                outcome TEXT DEFAULT 'pending',
                outcome_price REAL,
                outcome_gain REAL,
                peak_price REAL,
                peak_gain REAL,
                evaluated_at TIMESTAMP,
                outcome_reason TEXT
            )
        ''')
        
        # Create indexes for performance
        await self.conn.execute('CREATE INDEX IF NOT EXISTS idx_outcome ON signals(outcome)')
        await self.conn.execute('CREATE INDEX IF NOT EXISTS idx_posted_at ON signals(posted_at)')
        await self.conn.execute('CREATE INDEX IF NOT EXISTS idx_evaluated_at ON signals(evaluated_at)')
        
        await self.conn.commit()
        logger.info("✓ Database initialized")
    
    async def close(self):
        """Close database connection"""
        if self.conn:
            await self.conn.close()
    
    # ========================================================================
    # GENERAL METHODS
    # ========================================================================
    
    async def has_seen(self, address: str) -> bool:
        """Check if we've already seen this token"""
        query = "SELECT 1 FROM signals WHERE address = ?"
        
        async with self.conn.execute(query, (address,)) as cursor:
            row = await cursor.fetchone()
        
        return row is not None
    
    async def save_signal(self, token_data: dict, posted: bool = False):
        """Save a signal to database"""
        query = """
            INSERT OR REPLACE INTO signals 
            (address, symbol, name, initial_price, conviction_score, posted, posted_at, 
             posted_milestones, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        posted_at = datetime.now().isoformat() if posted else None
        initial_price = token_data.get('priceUsd', 0) if posted else 0
        
        await self.conn.execute(
            query,
            (
                token_data['address'],
                token_data.get('symbol', 'UNKNOWN'),
                token_data.get('name', 'Unknown Token'),
                initial_price,
                token_data.get('conviction_score', 0),
                1 if posted else 0,
                posted_at,
                '',
                'pending'
            )
        )
        await self.conn.commit()
    
    # ========================================================================
    # PERFORMANCE TRACKING METHODS
    # ========================================================================
    
    async def get_active_signals(self) -> List[Dict]:
        """Get signals that haven't hit 1000x yet"""
        query = """
            SELECT address, symbol, name, initial_price, posted_milestones, posted_at
            FROM signals
            WHERE posted = 1
            AND initial_price > 0
            AND (posted_milestones NOT LIKE '%1000%' OR posted_milestones IS NULL OR posted_milestones = '')
            ORDER BY posted_at DESC
            LIMIT 100
        """
        
        async with self.conn.execute(query) as cursor:
            rows = await cursor.fetchall()
        
        return [
            {
                'address': row[0],
                'symbol': row[1],
                'name': row[2],
                'initial_price': row[3],
                'posted_milestones': row[4] or '',
                'posted_at': row[5]
            }
            for row in rows
        ]
    
    async def update_posted_milestones(self, address: str, posted_milestones: str):
        """Update which milestones have been posted"""
        query = "UPDATE signals SET posted_milestones = ? WHERE address = ?"
        await self.conn.execute(query, (posted_milestones, address))
        await self.conn.commit()
    
    # ========================================================================
    # MOMENTUM ANALYZER METHODS
    # ========================================================================
    
    async def mark_sell_signal_sent(self, address: str, signal_type: str):
        """Mark that a sell signal has been sent"""
        query = "UPDATE signals SET last_sell_signal = ?, last_sell_signal_at = ? WHERE address = ?"
        await self.conn.execute(query, (signal_type, datetime.now().isoformat(), address))
        await self.conn.commit()
    
    async def get_sell_signal_history(self, address: str) -> Optional[Dict]:
        """Get the last sell signal sent for a token"""
        query = "SELECT last_sell_signal, last_sell_signal_at FROM signals WHERE address = ?"
        
        async with self.conn.execute(query, (address,)) as cursor:
            row = await cursor.fetchone()
        
        if row and row[0]:
            return {'signal_type': row[0], 'sent_at': row[1]}
        return None
    
    # ========================================================================
    # OUTCOME TRACKING METHODS
    # ========================================================================
    
    async def get_pending_outcomes(self) -> List[Dict]:
        """Get signals that don't have an outcome yet"""
        query = """
            SELECT address, symbol, initial_price, posted_at
            FROM signals
            WHERE posted = 1
            AND (outcome IS NULL OR outcome = 'pending')
            AND posted_at IS NOT NULL
            ORDER BY posted_at DESC
        """
        
        async with self.conn.execute(query) as cursor:
            rows = await cursor.fetchall()
        
        return [
            {
                'address': row[0],
                'symbol': row[1],
                'initial_price': row[2],
                'posted_at': row[3]
            }
            for row in rows
        ]
    
    async def save_outcome(self, address: str, outcome: str, outcome_price: float,
                          outcome_gain: float, peak_gain: float, 
                          evaluated_at: str, reason: str):
        """Save the outcome of a signal"""
        query = """
            UPDATE signals
            SET outcome = ?,
                outcome_price = ?,
                outcome_gain = ?,
                peak_gain = ?,
                evaluated_at = ?,
                outcome_reason = ?
            WHERE address = ?
        """
        
        await self.conn.execute(
            query, 
            (outcome, outcome_price, outcome_gain, peak_gain, evaluated_at, reason, address)
        )
        await self.conn.commit()
    
    async def get_peak_price(self, address: str) -> Optional[float]:
        """Get the peak price reached for a signal"""
        query = "SELECT peak_price FROM signals WHERE address = ?"
        
        async with self.conn.execute(query, (address,)) as cursor:
            row = await cursor.fetchone()
        
        return row[0] if row and row[0] else None
    
    async def update_peak_price(self, address: str, peak_price: float):
        """Update peak price if higher than current"""
        query = """
            UPDATE signals
            SET peak_price = ?
            WHERE address = ?
            AND (peak_price IS NULL OR peak_price < ?)
        """
        
        await self.conn.execute(query, (peak_price, address, peak_price))
        await self.conn.commit()
    
    async def get_outcomes(self, days: Optional[int] = None) -> List[Dict]:
        """Get all evaluated outcomes, optionally filtered by days"""
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            query = """
                SELECT address, symbol, outcome, outcome_gain, peak_gain, 
                       initial_price, outcome_price, evaluated_at, outcome_reason
                FROM signals
                WHERE outcome IS NOT NULL
                AND outcome != 'pending'
                AND posted_at >= ?
                ORDER BY evaluated_at DESC
            """
            
            async with self.conn.execute(query, (cutoff,)) as cursor:
                rows = await cursor.fetchall()
        else:
            query = """
                SELECT address, symbol, outcome, outcome_gain, peak_gain,
                       initial_price, outcome_price, evaluated_at, outcome_reason
                FROM signals
                WHERE outcome IS NOT NULL
                AND outcome != 'pending'
                ORDER BY evaluated_at DESC
            """
            
            async with self.conn.execute(query) as cursor:
                rows = await cursor.fetchall()
        
        return [
            {
                'address': row[0],
                'symbol': row[1],
                'outcome': row[2],
                'outcome_gain': row[3],
                'peak_gain': row[4],
                'initial_price': row[5],
                'outcome_price': row[6],
                'evaluated_at': row[7],
                'outcome_reason': row[8]
            }
            for row in rows
        ]
    
    async def count_pending_outcomes(self) -> int:
        """Count signals still pending outcome"""
        query = """
            SELECT COUNT(*) FROM signals
            WHERE posted = 1
            AND (outcome IS NULL OR outcome = 'pending')
        """
        
        async with self.conn.execute(query) as cursor:
            row = await cursor.fetchone()
        
        return row[0] if row else 0

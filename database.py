"""
Database class for Sentinel Signals
Handles all database operations with PostgreSQL
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger
import os


class Database:
    """PostgreSQL database handler"""
    
    def __init__(self, database_url: str = None):
        self.database_url = database_url or os.getenv('DATABASE_URL')
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable not set")
        
        self.pool = None
    
    async def initialize(self):
        """Initialize database connection pool"""
        try:
            # Create connection pool (min 1, max 10 connections)
            self.pool = SimpleConnectionPool(
                1, 10,
                self.database_url
            )
            logger.info("✓ Database connection pool initialized")
            
            # Test connection
            conn = self.pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                logger.info("✓ Database connection verified")
            finally:
                self.pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    
    async def close(self):
        """Close all database connections"""
        if self.pool:
            self.pool.closeall()
            logger.info("✓ Database connections closed")
    
    def _get_conn(self):
        """Get a connection from the pool"""
        return self.pool.getconn()
    
    def _put_conn(self, conn):
        """Return a connection to the pool"""
        self.pool.putconn(conn)
    
    # ========================================================================
    # GENERAL METHODS
    # ========================================================================
    
    async def has_seen(self, address: str) -> bool:
        """Check if we've already seen this token"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM signals WHERE address = %s", (address,))
                return cur.fetchone() is not None
        finally:
            self._put_conn(conn)
    
    async def get_signal(self, address: str) -> Optional[Dict]:
        """Get a signal by address"""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT address, symbol, name, initial_price, conviction_score, posted, posted_at
                    FROM signals
                    WHERE address = %s
                """, (address,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put_conn(conn)
    
    async def save_signal(self, token_data: dict, posted: bool = False):
        """Save a signal to database"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                posted_at = datetime.now() if posted else None
                initial_price = float(token_data.get('priceUsd', 0)) if posted else 0
                
                cur.execute("""
                    INSERT INTO signals 
                    (address, symbol, name, initial_price, conviction_score, posted, posted_at, 
                     posted_milestones, outcome)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (address) DO UPDATE SET
                        symbol = EXCLUDED.symbol,
                        name = EXCLUDED.name,
                        initial_price = EXCLUDED.initial_price,
                        conviction_score = EXCLUDED.conviction_score,
                        posted = EXCLUDED.posted,
                        posted_at = EXCLUDED.posted_at,
                        outcome = EXCLUDED.outcome
                """, (
                    token_data['address'],
                    token_data.get('symbol', 'UNKNOWN'),
                    token_data.get('name', 'Unknown Token'),
                    initial_price,
                    token_data.get('conviction_score', 0),
                    posted,
                    posted_at,
                    '',
                    'pending'
                ))
            conn.commit()
        finally:
            self._put_conn(conn)
    
    async def add_signal(
        self,
        address: str,
        symbol: str,
        name: str,
        conviction_score: float,
        initial_price: float,
        liquidity_usd: float,
        volume_24h: float,
        pair_address: str,
        message_id: int
    ):
        """Add a signal to database (called when signal is posted)"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO signals 
                    (address, symbol, name, initial_price, conviction_score, posted, posted_at, 
                     posted_milestones, outcome)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (address) DO UPDATE SET
                        symbol = EXCLUDED.symbol,
                        name = EXCLUDED.name,
                        initial_price = EXCLUDED.initial_price,
                        conviction_score = EXCLUDED.conviction_score,
                        posted = EXCLUDED.posted,
                        posted_at = EXCLUDED.posted_at
                """, (
                    address,
                    symbol,
                    name,
                    initial_price,
                    int(conviction_score),
                    1,  # posted = true
                    datetime.now(),
                    '',  # no milestones yet
                    'pending'
                ))
            conn.commit()
            logger.info(f"✓ Saved signal to database: {symbol} ({address[:8]}...)")
        finally:
            self._put_conn(conn)
    
    # ========================================================================
    # PERFORMANCE TRACKING METHODS
    # ========================================================================
    
    async def get_active_signals(self) -> List[Dict]:
        """Get signals that haven't hit 1000x yet"""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT address, symbol, name, initial_price, posted_milestones, posted_at
                    FROM signals
                    WHERE posted = 1
                    AND initial_price > 0
                    AND (posted_milestones NOT LIKE %s OR posted_milestones IS NULL OR posted_milestones = '')
                    ORDER BY posted_at DESC
                    LIMIT 100
                """, ('%1000%',))
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        finally:
            self._put_conn(conn)
    
    async def update_posted_milestones(self, address: str, posted_milestones: str):
        """Update which milestones have been posted"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE signals SET posted_milestones = %s WHERE address = %s",
                    (posted_milestones, address)
                )
            conn.commit()
        finally:
            self._put_conn(conn)
    
    # ========================================================================
    # MOMENTUM ANALYZER METHODS
    # ========================================================================
    
    async def mark_sell_signal_sent(self, address: str, signal_type: str):
        """Mark that a sell signal has been sent"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE signals SET last_sell_signal = %s, last_sell_signal_at = %s WHERE address = %s",
                    (signal_type, datetime.now(), address)
                )
            conn.commit()
        finally:
            self._put_conn(conn)
    
    async def get_sell_signal_history(self, address: str) -> Optional[Dict]:
        """Get the last sell signal sent for a token"""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT last_sell_signal, last_sell_signal_at FROM signals WHERE address = %s",
                    (address,)
                )
                row = cur.fetchone()
                if row and row['last_sell_signal']:
                    return {'signal_type': row['last_sell_signal'], 'sent_at': row['last_sell_signal_at']}
                return None
        finally:
            self._put_conn(conn)
    
    # ========================================================================
    # OUTCOME TRACKING METHODS
    # ========================================================================
    
    async def get_pending_outcomes(self) -> List[Dict]:
        """Get signals that don't have an outcome yet"""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT address, symbol, initial_price, posted_at
                    FROM signals
                    WHERE posted = 1
                    AND (outcome IS NULL OR outcome = 'pending')
                    AND posted_at IS NOT NULL
                    ORDER BY posted_at DESC
                """)
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        finally:
            self._put_conn(conn)
    
    async def save_outcome(self, address: str, outcome: str, outcome_price: float,
                          outcome_gain: float, peak_gain: float, 
                          evaluated_at: str, reason: str):
        """Save the outcome of a signal"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE signals
                    SET outcome = %s,
                        outcome_price = %s,
                        outcome_gain = %s,
                        peak_gain = %s,
                        evaluated_at = %s,
                        outcome_reason = %s
                    WHERE address = %s
                """, (outcome, outcome_price, outcome_gain, peak_gain, evaluated_at, reason, address))
            conn.commit()
        finally:
            self._put_conn(conn)
    
    async def get_peak_price(self, address: str) -> Optional[float]:
        """Get the peak price reached for a signal"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT peak_price FROM signals WHERE address = %s", (address,))
                row = cur.fetchone()
                return float(row[0]) if row and row[0] else None
        finally:
            self._put_conn(conn)
    
    async def update_peak_price(self, address: str, peak_price: float):
        """Update peak price if higher than current"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE signals
                    SET peak_price = %s
                    WHERE address = %s
                    AND (peak_price IS NULL OR peak_price < %s)
                """, (peak_price, address, peak_price))
            conn.commit()
        finally:
            self._put_conn(conn)
    
    async def get_outcomes(self, days: Optional[int] = None) -> List[Dict]:
        """Get all evaluated outcomes, optionally filtered by days"""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if days:
                    cutoff = datetime.now() - timedelta(days=days)
                    cur.execute("""
                        SELECT address, symbol, outcome, outcome_gain, peak_gain, 
                               initial_price, outcome_price, evaluated_at, outcome_reason
                        FROM signals
                        WHERE outcome IS NOT NULL
                        AND outcome != 'pending'
                        AND posted_at >= %s
                        ORDER BY evaluated_at DESC
                    """, (cutoff,))
                else:
                    cur.execute("""
                        SELECT address, symbol, outcome, outcome_gain, peak_gain,
                               initial_price, outcome_price, evaluated_at, outcome_reason
                        FROM signals
                        WHERE outcome IS NOT NULL
                        AND outcome != 'pending'
                        ORDER BY evaluated_at DESC
                    """)
                
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        finally:
            self._put_conn(conn)
    
    async def count_pending_outcomes(self) -> int:
        """Count signals still pending outcome"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM signals
                    WHERE posted = 1
                    AND (outcome IS NULL OR outcome = 'pending')
                """)
                row = cur.fetchone()
                return row[0] if row else 0
        finally:
            self._put_conn(conn)

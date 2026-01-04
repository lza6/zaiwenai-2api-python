import asyncio
import time
import os
from typing import Optional
from app.core.database import get_db_connection, DB_PATH
from app.utils.logger import logger

TOKEN_FILE = "data/tokens.txt"

class AccountManager:
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AccountManager, cls).__new__(cls)
        return cls._instance

    async def initialize(self):
        """Import tokens from text file if provided."""
        if not os.path.exists(TOKEN_FILE):
            logger.warning(f"⚠️ [Token] No token file found at {TOKEN_FILE}. Ensuring DB has accounts.")
            return

        # Check if we need to import
        # We blindly import new uniques
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            
            if not lines:
                return

            async with get_db_connection() as db:
                count = 0
                for token in lines:
                    try:
                        await db.execute("INSERT OR IGNORE INTO accounts (token) VALUES (?)", (token,))
                        count += 1
                    except Exception as e:
                        logger.error(f"❌ [Token] Failed to import token: {e}")
                await db.commit()
                if count > 0:
                    logger.info(f"✅ [Token] Imported {count} tokens from {TOKEN_FILE}")
        except Exception as e:
            logger.error(f"❌ [Token] Error reading token file: {e}")

    async def get_token(self) -> Optional[str]:
        """
        Get an active token using Least-Recently-Used logic for simple load balancing.
        """
        async with self._lock:
            async with get_db_connection() as db:
                # 先获取总数用于日志
                count_cursor = await db.execute("SELECT COUNT(*) FROM accounts WHERE status = 'active'")
                count_row = await count_cursor.fetchone()
                active_count = count_row[0] if count_row else 0
                
                cursor = await db.execute("""
                    SELECT token FROM accounts 
                    WHERE status = 'active' 
                    ORDER BY last_used_at ASC 
                    LIMIT 1
                """)
                row = await cursor.fetchone()
                if row:
                    token = row[0]
                    # Update usage time immediately
                    await db.execute("UPDATE accounts SET last_used_at = ? WHERE token = ?", (time.time(), token))
                    await db.commit()
                    logger.debug(f"📤 [Token] Retrieved token: {token[:8]}... (pool size: {active_count})")
                    return token
                else:
                    logger.error(f"🚫 [Token] No active tokens available in database! (pool size: {active_count})")
                    return None

    async def update_token(self, old_token: str, new_token: str):
        """ Update a token if it has changed (token rotation). """
        if old_token == new_token:
            logger.debug(f"⏩ [Token] Skip update - tokens are identical: {old_token[:8]}...")
            return

        async with self._lock:
            async with get_db_connection() as db:
                try:
                    logger.info(f"🔄 [Token] Rotating token: {old_token[:8]}... -> {new_token[:8]}...")
                    
                    # Check if new token already exists
                    cursor = await db.execute("SELECT id FROM accounts WHERE token = ?", (new_token,))
                    exists = await cursor.fetchone()
                    
                    if not exists:
                        # Replace old with new
                        await db.execute("UPDATE accounts SET token = ?, last_used_at = ? WHERE token = ?", (new_token, time.time(), old_token))
                        await db.commit()
                        logger.info(f"✅ [Token] Token rotated successfully!")
                        logger.info(f"   Old: {old_token[:16]}...")
                        logger.info(f"   New: {new_token[:16]}...")
                    else:
                        # If new already exists, mark old as rotated
                        logger.info(f"ℹ️ [Token] New token already exists in pool. Marking old as rotated.")
                        await db.execute("UPDATE accounts SET status = 'rotated' WHERE token = ?", (old_token,))
                        await db.commit()
                except Exception as e:
                    logger.error(f"❌ [Token] Error updating token: {e}")

    async def mark_invalid(self, token: str):
        async with self._lock:
            async with get_db_connection() as db:
                await db.execute("UPDATE accounts SET status = 'invalid' WHERE token = ?", (token,))
                await db.commit()
                logger.warning(f"⚠️ [Token] Marked token as invalid: {token[:8]}...")
                
                # Log remaining active tokens
                cursor = await db.execute("SELECT COUNT(*) FROM accounts WHERE status = 'active'")
                row = await cursor.fetchone()
                remaining = row[0] if row else 0
                logger.info(f"📊 [Token] Remaining active tokens: {remaining}")

    async def get_stats(self) -> dict:
        """Get token pool statistics."""
        async with get_db_connection() as db:
            cursor = await db.execute("""
                SELECT status, COUNT(*) as count FROM accounts GROUP BY status
            """)
            rows = await cursor.fetchall()
            stats = {row[0]: row[1] for row in rows}
            return stats

account_manager = AccountManager()


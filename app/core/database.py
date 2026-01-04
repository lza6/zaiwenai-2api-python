import aiosqlite
import os
from typing import Optional, List, Dict, Any
from app.utils.logger import logger

DB_PATH = "data/accounts.db"

async def init_db():
    """Initialize the database and tables."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'active', -- active, exhausted, invalid
                last_used_at REAL DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)
        await db.commit()
    logger.info("Database initialized.")

def get_db_connection():
    """Get a database connection."""
    # usage: async with get_db_connection() as db:
    return aiosqlite.connect(DB_PATH)

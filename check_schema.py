
import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.getcwd())
load_dotenv('.env.local')

import database as db

async def check():
    await db.create_pool()
    async with db.pool.acquire() as conn:
        print("Checking ai_chat_sessions columns:")
        rows = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'ai_chat_sessions'")
        columns = [r['column_name'] for r in rows]
        print(columns)
        
        # Check if table exists at all
        if not columns:
             print("Table ai_chat_sessions NOT FOUND!")
        else:
             if 'worker_id' not in columns:
                 print("worker_id column MISSING!")
                 # Optional: Fix it
                 if len(sys.argv) > 1 and sys.argv[1] == '--fix':
                     print("Attemping to fix...")
                     await conn.execute("ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS worker_id INTEGER REFERENCES workers(id) ON DELETE SET NULL;")
                     print("Column added.")
             else:
                 print("worker_id column EXISTS.")
                 
    await db.pool.close()

if __name__ == "__main__":
    asyncio.run(check())

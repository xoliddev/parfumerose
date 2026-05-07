
import asyncio
import sys
import os

# Add root directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db

async def check_data():
    await db.create_pool()
    async with db.pool.acquire() as conn:
        # Check counts
        emp_count = await conn.fetchval("SELECT COUNT(*) FROM workers")
        order_count = await conn.fetchval("SELECT COUNT(*) FROM orders")
        mat_count = await conn.fetchval("SELECT COUNT(*) FROM materials")
        
        print(f"Employees: {emp_count}")
        print(f"Orders: {order_count}")
        print(f"Materials: {mat_count}")
        
        # If we have materials, print a few names to test search
        if mat_count > 0:
            mats = await conn.fetch("SELECT name FROM materials LIMIT 3")
            print("Sample Materials:", [m['name'] for m in mats])
            
        if order_count > 0:
            orders = await conn.fetch("SELECT order_name FROM orders LIMIT 3")
            print("Sample Orders:", [o['order_name'] for o in orders])

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check_data())

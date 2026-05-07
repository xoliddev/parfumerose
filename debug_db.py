import asyncio
import database

async def check_db():
    await database.create_pool()
    async with database.pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, tg_id, status, notification_message_id, notification_chat_id FROM job_applications ORDER BY created_at DESC LIMIT 5")
        for row in rows:
            print(dict(row))

if __name__ == "__main__":
    asyncio.run(check_db())

"""
Migratsiya: Eski to'lovlarning vaqtini to'g'irlash
"""

import asyncio
import asyncpg
import config

async def fix_payment_times():
    # Database connection using config
    conn = await asyncpg.connect(config.DATABASE_URL)
    
    try:
        # Check how many records need fixing
        count = await conn.fetchval("""
            SELECT COUNT(*) 
            FROM salary_payments 
            WHERE DATE(payment_time) != payment_date
        """)
        
        print(f"📊 Tuzatish kerak bo'lgan to'lovlar: {count}")
        
        if count == 0:
            print("✅ Barcha to'lovlar to'g'ri!")
            return
        
        # Fix the times - set payment_time to payment_date at midnight UTC
        # Then add current time for a reasonable timestamp
        result = await conn.execute("""
            UPDATE salary_payments 
            SET payment_time = payment_date::timestamp + '12:00:00'::time
            WHERE DATE(payment_time) != payment_date
        """)
        
        print(f"✅ {count} ta to'lov vaqti to'g'irlandi!")
        print(f"🔄 Natija: {result}")
        
    except Exception as e:
        print(f"❌ Xatolik: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    print("🔧 To'lovlar vaqtini tuzatish boshlandi...")
    asyncio.run(fix_payment_times())
    print("✅ Tayyor!")

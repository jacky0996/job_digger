import asyncio
import os

import aiomysql
from dotenv import load_dotenv

load_dotenv()


async def update_db():
    try:
        conn = await aiomysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 3308)),
            user=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            db=os.getenv("DB_DATABASE"),
            autocommit=True,
        )
        async with conn.cursor() as cur:
            # 確保時區正確
            await cur.execute("SET time_zone = '+08:00'")

            # 增加欄位
            try:
                sql = (
                    "ALTER TABLE vacancies ADD COLUMN updated_at "
                    "TIMESTAMP DEFAULT CURRENT_TIMESTAMP "
                    "ON UPDATE CURRENT_TIMESTAMP"
                )
                await cur.execute(sql)
                print("✅ Added updated_at")
            except Exception:
                pass

            try:
                await cur.execute(
                    "ALTER TABLE vacancies ADD COLUMN deleted_at "
                    "TIMESTAMP NULL DEFAULT NULL"
                )
                print("✅ Added deleted_at")
            except Exception:
                pass

            print("🚀 Schema update complete.")
        conn.close()
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(update_db())

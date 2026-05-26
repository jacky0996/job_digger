"""一次性 schema migration 草稿(PostgreSQL 版)。

歷史:舊版用來幫舊環境的 vacancies 補 updated_at / deleted_at 兩個欄位。
PostgreSQL 切換後,這兩個欄位已內建在 init.sql,新環境用不到。
留檔僅作示範,未來臨時要補欄位再改 SQL 即可。
"""

import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def update_db():
    try:
        conn = await asyncpg.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 5434)),
            user=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_DATABASE"),
            server_settings={"timezone": "+08:00"},
        )

        # PG 用 IF NOT EXISTS 取代 try/except 包 ALTER TABLE
        try:
            await conn.execute(
                "ALTER TABLE vacancies "
                "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP "
                "DEFAULT CURRENT_TIMESTAMP"
            )
            print("✅ Ensured updated_at column")
        except Exception as e:
            print(f"updated_at: {e}")

        try:
            await conn.execute(
                "ALTER TABLE vacancies "
                "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP DEFAULT NULL"
            )
            print("✅ Ensured deleted_at column")
        except Exception as e:
            print(f"deleted_at: {e}")

        print("🚀 Schema update complete.")
        await conn.close()
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(update_db())

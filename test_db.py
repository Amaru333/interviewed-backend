import asyncio
from database import get_db, Session as SessionModel, SessionScore
from sqlalchemy import select

async def main():
    async for db in get_db():
        result = await db.execute(select(SessionScore).limit(1))
        score = result.scalar_one_or_none()
        if score:
            print(f"Session with score found: {score.session_id}")
        else:
            print("No scores found.")
        break

if __name__ == "__main__":
    asyncio.run(main())

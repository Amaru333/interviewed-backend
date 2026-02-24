import asyncio
from database import get_db, Session, Message
from routes.session_routes import _generate_session_scores
from sqlalchemy import select

async def main():
    async for db in get_db():
        # Find a session with messages
        result = await db.execute(select(Session).limit(10))
        sessions = result.scalars().all()
        for s in sessions:
            msg_res = await db.execute(select(Message).where(Message.session_id == s.id))
            msgs = msg_res.scalars().all()
            if len(msgs) > 0:
                print(f"Testing score generation for session {s.id}...")
                await _generate_session_scores(db, s.id)
                print("Done!")
                return
        print("No sessions with messages found.")

if __name__ == "__main__":
    asyncio.run(main())

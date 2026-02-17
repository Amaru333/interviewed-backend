from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
import uuid
import json
import time
from datetime import datetime
from jose import jwt, JWTError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from database import init_db, async_session, Session as SessionModel, User, Message
from interview_nova_sonic import InterviewNovaSonic, CHUNK_SIZE
from auth import SECRET_KEY, ALGORITHM
from routes.auth_routes import router as auth_router
from routes.session_routes import router as session_router
from routes.resume_routes import router as resume_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="Interviewed API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router)
app.include_router(session_router)
app.include_router(resume_router)


class InterviewConnectionManager:
    """Manages a single WebSocket interview session."""

    def __init__(self, session_id: str, user_id: str):
        self.session_id = session_id
        self.user_id = user_id
        self.nova_client: InterviewNovaSonic | None = None
        self.active_connection: WebSocket | None = None
        self.audio_content_started = False
        self.last_audio_chunk_time = 0
        self.audio_chunk_threshold = 0.1
        self.chat_history = []
        self.max_history = 10
        # Message accumulation buffers
        self.current_user_message = ""
        self.current_assistant_message = ""
        self.last_message_role = None

    def add_history(self, role: str, text: str):
        content_name = str(uuid.uuid4())
        self.chat_history.append({"role": role, "text": text, "contentName": content_name})
        if len(self.chat_history) > self.max_history:
            self.chat_history = self.chat_history[-self.max_history:]

    async def save_message(self, role: str, content: str):
        """Save a message to the database."""
        async with async_session() as db:
            msg_id = str(uuid.uuid4())
            now = datetime.utcnow()
            msg = Message(
                id=msg_id,
                session_id=self.session_id,
                role=role,
                content=content,
                timestamp=now,
            )
            db.add(msg)
            await db.commit()

    async def connect(self, websocket: WebSocket, resume_text: str, job_description: str, company_name: str, role_title: str):
        await websocket.accept()
        self.active_connection = websocket
        logger.info(f"WebSocket accepted for session {self.session_id}")

        # Update session status to active
        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(SessionModel).where(SessionModel.id == self.session_id)
            )
            session = result.scalar_one_or_none()
            if session:
                session.status = "active"
                await db.commit()

        # Create Nova Sonic client with interview context
        self.nova_client = InterviewNovaSonic(
            resume_text=resume_text,
            job_description=job_description,
            company_name=company_name,
            role_title=role_title,
        )
        await self.nova_client.start_session()
        logger.info("Interview Nova Sonic session started")

        # Replay conversation history
        history = self.chat_history.copy()
        while history and history[0]["role"] != "USER":
            history.pop(0)
        if history:
            for msg in history:
                content_name = msg["contentName"]
                role = msg["role"]
                text = msg["text"]
                content_start = json.dumps({
                    "event": {
                        "contentStart": {
                            "promptName": self.nova_client.prompt_name,
                            "contentName": content_name,
                            "type": "TEXT",
                            "interactive": False,
                            "role": role,
                            "textInputConfiguration": {"mediaType": "text/plain"},
                        }
                    }
                })
                await self.nova_client.send_event(content_start)
                text_input = json.dumps({
                    "event": {
                        "textInput": {
                            "promptName": self.nova_client.prompt_name,
                            "contentName": content_name,
                            "content": text,
                        }
                    }
                })
                await self.nova_client.send_event(text_input)
                content_end = json.dumps({
                    "event": {
                        "contentEnd": {
                            "promptName": self.nova_client.prompt_name,
                            "contentName": content_name,
                        }
                    }
                })
                await self.nova_client.send_event(content_end)
        
        # Auto-start: Send initial greeting trigger to make AI introduce itself
        # This makes the AI start talking immediately without waiting for user input
        if not history:  # Only send greeting trigger if no conversation history
            greeting_content_name = str(uuid.uuid4())
            greeting_start = json.dumps({
                "event": {
                    "contentStart": {
                        "promptName": self.nova_client.prompt_name,
                        "contentName": greeting_content_name,
                        "type": "TEXT",
                        "interactive": False,
                        "role": "USER",
                        "textInputConfiguration": {"mediaType": "text/plain"},
                    }
                }
            })
            await self.nova_client.send_event(greeting_start)
            
            greeting_text = json.dumps({
                "event": {
                    "textInput": {
                        "promptName": self.nova_client.prompt_name,
                        "contentName": greeting_content_name,
                        "content": "Please introduce yourself and begin the interview.",
                    }
                }
            })
            await self.nova_client.send_event(greeting_text)
            
            greeting_end = json.dumps({
                "event": {
                    "contentEnd": {
                        "promptName": self.nova_client.prompt_name,
                        "contentName": greeting_content_name,
                    }
                }
            })
            await self.nova_client.send_event(greeting_end)


    async def disconnect(self):
        # Save any remaining accumulated messages
        if self.current_user_message.strip():
            await self.save_message("USER", self.current_user_message.strip())
            self.add_history("USER", self.current_user_message.strip())
            self.current_user_message = ""
        if self.current_assistant_message.strip():
            await self.save_message("ASSISTANT", self.current_assistant_message.strip())
            self.add_history("ASSISTANT", self.current_assistant_message.strip())
            self.current_assistant_message = ""
        
        if self.nova_client:
            logger.info("Stopping interview session")
            if self.audio_content_started:
                await self.stop_audio()
            self.nova_client.is_active = False
            await self.nova_client.end_session()
            self.nova_client = None
        self.active_connection = None

    async def receive_audio(self, audio_data: bytes):
        if self.nova_client and self.audio_content_started:
            try:
                current_time = time.time()
                if current_time - self.last_audio_chunk_time >= self.audio_chunk_threshold:
                    await self.nova_client.send_audio_chunk(audio_data)
                    self.last_audio_chunk_time = current_time
            except Exception as e:
                logger.error(f"Error sending audio chunk: {e}")

    async def start_audio(self):
        if self.nova_client and not self.audio_content_started:
            try:
                logger.info("Starting audio input")
                self.nova_client.audio_content_name = str(uuid.uuid4())
                await self.nova_client.start_audio_input()
                self.audio_content_started = True
            except Exception as e:
                logger.error(f"Error starting audio: {e}")

    async def stop_audio(self):
        if self.nova_client and self.audio_content_started:
            try:
                logger.info("Stopping audio input")
                await self.nova_client.end_audio_input()
                self.audio_content_started = False
            except Exception as e:
                logger.error(f"Error stopping audio: {e}")

    async def process_audio_responses(self):
        if not self.nova_client or not self.active_connection:
            return
        logger.info("Started processing audio responses")
        try:
            while self.nova_client.is_active:
                try:
                    audio_data = await asyncio.wait_for(
                        self.nova_client.audio_queue.get(), timeout=0.1
                    )
                    if audio_data:
                        if self.nova_client.barge_in:
                            continue
                        for i in range(0, len(audio_data), CHUNK_SIZE):
                            if self.nova_client.barge_in:
                                break
                            chunk = audio_data[i : min(i + CHUNK_SIZE, len(audio_data))]
                            await self.active_connection.send_bytes(chunk)
                            await asyncio.sleep(0.001)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error processing audio response: {e}")
                    await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Audio response loop error: {e}")

    async def process_events(self):
        if not self.nova_client or not self.active_connection:
            return
        try:
            while self.nova_client.is_active:
                try:
                    event_json = await asyncio.wait_for(
                        self.nova_client.event_queue.get(), timeout=1.0
                    )
                    if event_json:
                        event_data = json.loads(event_json)
                        if "event" in event_data and "textOutput" in event_data["event"]:
                            text_content = event_data["event"]["textOutput"].get("content", "")
                            role = event_data["event"]["textOutput"].get("role", "ASSISTANT")

                            # Check for barge-in first
                            if '{ "interrupted" : true }' in text_content:
                                logger.info("Barge-in detected")
                                self.nova_client.barge_in = True
                                barge_in_event = json.dumps({
                                    "event": {"bargeIn": {"status": "interrupted"}}
                                })
                                await self.active_connection.send_text(barge_in_event)
                                # Clear current message on barge-in
                                if self.last_message_role == "ASSISTANT":
                                    self.current_assistant_message = ""
                                continue

                            # Detect role change - save accumulated message from previous speaker
                            if self.last_message_role and self.last_message_role != role:
                                if self.last_message_role == "USER" and self.current_user_message.strip():
                                    await self.save_message("USER", self.current_user_message.strip())
                                    self.add_history("USER", self.current_user_message.strip())
                                    self.current_user_message = ""
                                elif self.last_message_role == "ASSISTANT" and self.current_assistant_message.strip():
                                    await self.save_message("ASSISTANT", self.current_assistant_message.strip())
                                    self.add_history("ASSISTANT", self.current_assistant_message.strip())
                                    self.current_assistant_message = ""

                            # Accumulate current chunk
                            if role == "USER":
                                self.current_user_message += text_content
                            else:  # ASSISTANT
                                self.current_assistant_message += text_content

                            # Update last role
                            self.last_message_role = role

                        await self.active_connection.send_text(event_json)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error processing event: {e}")
                    await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Event loop error: {e}")


# Active interview managers
active_interviews: dict[str, InterviewConnectionManager] = {}


def verify_ws_token(token: str) -> str | None:
    """Verify JWT token from WebSocket query param."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


@app.websocket("/ws/interview/{session_id}")
async def interview_websocket(websocket: WebSocket, session_id: str, token: str = Query("")):
    # Authenticate
    user_id = verify_ws_token(token)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Verify session belongs to user
    async with async_session() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(SessionModel).where(
                SessionModel.id == session_id, SessionModel.user_id == user_id
            )
        )
        session_row = result.scalar_one_or_none()
        if not session_row:
            await websocket.close(code=4004, reason="Session not found")
            return

        job_description = session_row.job_description
        company_name = session_row.company_name or ""
        role_title = session_row.role_title or ""

        # Get user resume
        result = await db.execute(select(User).where(User.id == user_id))
        user_row = result.scalar_one_or_none()
        resume_text = user_row.resume_text if user_row and user_row.resume_text else ""

    # Create interview manager
    manager = InterviewConnectionManager(session_id, user_id)
    active_interviews[session_id] = manager

    await manager.connect(websocket, resume_text, job_description, company_name, role_title)

    # Send init event
    await websocket.send_text(json.dumps({
        "event": {
            "init": {
                "sessionId": session_id,
                "status": "connected",
            }
        }
    }))

    process_task = asyncio.create_task(manager.process_audio_responses())
    event_task = asyncio.create_task(manager.process_events())

    try:
        while True:
            message = await websocket.receive()
            if "bytes" in message:
                await manager.receive_audio(message["bytes"])
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    if "event" in data:
                        event = data["event"]
                        if "textInput" in event:
                            user_text = event["textInput"].get("content", "")
                            # Accumulate user text input
                            if manager.last_message_role and manager.last_message_role != "USER":
                                # Role changed, save previous assistant message
                                if manager.current_assistant_message.strip():
                                    await manager.save_message("ASSISTANT", manager.current_assistant_message.strip())
                                    manager.add_history("ASSISTANT", manager.current_assistant_message.strip())
                                    manager.current_assistant_message = ""
                            manager.current_user_message += user_text
                            manager.last_message_role = "USER"
                    else:
                        command = message["text"]
                        if command == "start_audio":
                            await manager.start_audio()
                        elif command == "stop_audio":
                            await manager.stop_audio()
                        elif command == "end_interview":
                            break
                except json.JSONDecodeError:
                    command = message["text"]
                    if command == "start_audio":
                        await manager.start_audio()
                    elif command == "stop_audio":
                        await manager.stop_audio()
                    elif command == "end_interview":
                        break
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        process_task.cancel()
        event_task.cancel()
        await manager.disconnect()
        active_interviews.pop(session_id, None)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "interviewed", "uptime": time.time(), "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Interviewed API server")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
import uuid
import json
import time
import re
from datetime import datetime
from jose import jwt, JWTError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from database import init_db, async_session, Session as SessionModel, User, Message
from interview_nova_sonic import InterviewNovaSonic, pick_random_persona, PANEL_PERSONAS, CHUNK_SIZE
from auth import SECRET_KEY, ALGORITHM
from routes.auth_routes import router as auth_router
from routes.session_routes import router as session_router
from routes.resume_routes import router as resume_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Text injected as the auto-start kick-off so the AI introduces itself immediately.
# We keep a reference to this exact string so process_events can filter it from
# the saved transcript if Nova Sonic ever echoes the text input back as a textOutput.
GREETING_TRIGGER_MARKER = "Please introduce yourself and begin the interview."

# Separate trigger for auto-reconnect — instructs the AI to continue seamlessly
# without re-introducing itself so the user doesn't notice the reconnection.
RECONNECT_TRIGGER_MARKER = "Continue the interview from where we left off. Do NOT re-introduce yourself or greet the candidate again — just pick up naturally with your next question."

# Phrases that indicate the AI has wrapped up the interview
_INTERVIEW_COMPLETE_PHRASES = [
    "interview is complete",
    "interview is now complete",
    "that concludes our interview",
    "that's all the questions",
    "those are all my questions",
    "we've covered everything",
    "thank you for your time today",
    "best of luck",
    "good luck with",
    "this concludes",
    "we're all done here",
    "we are all done here",
    "interview has come to an end",
    "end of our interview",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="Interviewed API", lifespan=lifespan)

# CORS Configuration
ALLOWED_ORIGINS = [
    "http://localhost:3000",       # Next.js Default
    "http://127.0.0.1:3000",
    "https://interviewed.space",    # Production Frontend (assumed)
    "https://www.interviewed.space",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
        self.chat_history = []
        self.max_history = 30
        # Message accumulation buffers
        self.current_user_message = ""
        self.current_assistant_message = ""
        self.last_message_role = None
        # Session params stored for auto-reconnect
        self._resume_text = ""
        self._job_description = ""
        self._company_name = ""
        self._role_title = ""
        self._persona: dict | None = None
        self._interview_type: str = "solo"
        # Reconnect coordination
        self._reconnect_done = asyncio.Event()
        self._should_reconnect = False
        self._consecutive_reconnect_failures = 0
        # Panel tracking
        self._active_panelist: str | None = None
        self._panel_names: set[str] = set()
        # Panel orchestration (multi-agent rotation)
        self._panel_rotation: list[dict] = []  # list of panelist persona dicts
        self._panel_index: int = 0
        self._panel_user_responses: int = 0  # user answers for current panelist
        self._questions_per_panelist: int = 2
        self._switching_panelist: bool = False

    def add_history(self, role: str, text: str):
        content_name = str(uuid.uuid4())
        self.chat_history.append({"role": role, "text": text, "contentName": content_name})
        if len(self.chat_history) > self.max_history:
            self.chat_history = self.chat_history[-self.max_history:]

    # Nova Sonic's text-history context window is limited.  Replaying too many
    # messages causes "Chat history is over max limit".  We keep only the most
    # recent turns and ensure the list starts with a USER message (required).
    MAX_REPLAY_MESSAGES = 4

    async def _load_history_from_db(self) -> list[dict]:
        """Load conversation history from DB, trimmed to fit Nova Sonic's limit."""
        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Message)
                .where(Message.session_id == self.session_id)
                .order_by(Message.timestamp.asc())
            )
            rows = result.scalars().all()
            all_msgs = [
                {"role": m.role, "text": m.content, "contentName": str(uuid.uuid4())}
                for m in rows
            ]
            # Trim to the most recent N messages
            if len(all_msgs) > self.MAX_REPLAY_MESSAGES:
                all_msgs = all_msgs[-self.MAX_REPLAY_MESSAGES:]
            # History must start with a USER message per Nova Sonic docs
            while all_msgs and all_msgs[0]["role"] != "USER":
                all_msgs.pop(0)
            
            # Truncate any single message to 800 characters to prevent
            # corrupted/repeating sessions from permanently maxing out AWS context window
            for msg in all_msgs:
                if len(msg["text"]) > 800:
                    msg["text"] = msg["text"][-800:]

            return all_msgs

    # Maximum bytes per single history message (matches AWS reference pattern)
    _MAX_SINGLE_MSG_BYTES = 1024
    _MAX_HISTORY_BYTES = 40960  # ~40KB total history cap

    async def _replay_history(self, history: list[dict]):
        """Send conversation history to Nova Sonic as non-interactive text context.

        Following the resume-conversation reference pattern:
        - Each message is a separate contentStart/textInput/contentEnd triplet
        - role is set in contentStart, interactive=false
        - Messages are truncated to _MAX_SINGLE_MSG_BYTES
        - Total history is capped at _MAX_HISTORY_BYTES
        """
        total_bytes = 0
        for msg in history:
            content_name = msg["contentName"]
            role = msg["role"]
            text = msg["text"]

            # Truncate individual messages that exceed byte limit
            text_bytes = text.encode('utf-8')
            if len(text_bytes) > self._MAX_SINGLE_MSG_BYTES:
                text = text_bytes[:self._MAX_SINGLE_MSG_BYTES].decode('utf-8', errors='ignore') + '... [truncated]'

            # Check total history size cap
            msg_size = len(text.encode('utf-8')) + len(role.encode('utf-8'))
            if total_bytes + msg_size > self._MAX_HISTORY_BYTES:
                logger.info(f"History replay capped at {total_bytes} bytes ({len(history)} messages)")
                break
            total_bytes += msg_size

            await self.nova_client.send_event(json.dumps({
                "event": {"contentStart": {
                    "promptName": self.nova_client.prompt_name,
                    "contentName": content_name,
                    "type": "TEXT",
                    "interactive": False,
                    "role": role,
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }}
            }))
            await self.nova_client.send_event(json.dumps({
                "event": {"textInput": {
                    "promptName": self.nova_client.prompt_name,
                    "contentName": content_name,
                    "content": text,
                }}
            }))
            await self.nova_client.send_event(json.dumps({
                "event": {"contentEnd": {
                    "promptName": self.nova_client.prompt_name,
                    "contentName": content_name,
                }}
            }))
            await asyncio.sleep(0.01)  # Small delay between history blocks

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

        interview_type = "solo"
        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(SessionModel).where(SessionModel.id == self.session_id)
            )
            sess = result.scalar_one_or_none()
            if sess:
                sess.status = "active"
                await db.commit()
                interview_type = getattr(sess, "interview_type", "solo") or "solo"
        self._interview_type = interview_type

        # Pick a random interviewer persona for this session
        persona = pick_random_persona()
        self._persona = persona

        # For panel mode, set up multi-agent rotation
        if self._interview_type == "panel":
            self._panel_names = {p["name"] for p in PANEL_PERSONAS}
            self._panel_rotation = list(PANEL_PERSONAS)
            self._panel_index = 0
            self._panel_user_responses = 0
            # Use the first panelist's persona (with their distinct voice)
            persona = self._panel_rotation[0]
            self._persona = persona
            self._active_panelist = persona["name"]

        # Store session params for auto-reconnect
        self._resume_text = resume_text
        self._job_description = job_description
        self._company_name = company_name
        self._role_title = role_title

        # Create Nova Sonic client with interview context and persona.
        # on_timeout sets _should_reconnect=True synchronously before is_active=False
        # so process_events sees the flag before its inner while-loop condition fails.
        self.nova_client = InterviewNovaSonic(
            resume_text=resume_text,
            job_description=job_description,
            company_name=company_name,
            role_title=role_title,
            persona=persona,
            interview_type=interview_type,
            panelist_index=self._panel_index if interview_type == "panel" else 0,
            panel_total=len(self._panel_rotation) if interview_type == "panel" else 1,
            on_timeout=lambda: setattr(self, "_should_reconnect", True),
        )
        self.interviewer_name = persona["name"]

        # Store interviewer info on the session record
        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(SessionModel).where(SessionModel.id == self.session_id)
            )
            session = result.scalar_one_or_none()
            if session:
                session.interviewer_name = persona["name"]
                session.interviewer_voice = persona["voice"]
                await db.commit()
        await self.nova_client.start_session()
        logger.info("Interview Nova Sonic session started")

        # Replay conversation history (trimmed to fit Nova Sonic's context limit)
        history = await self._load_history_from_db()
        if history:
            await self._replay_history(history)
        
        # Auto-start: inject a text input to trigger the AI to introduce itself.
        # Per the speaks-first reference pattern, we use interactive=True so Nova Sonic
        # treats this as real user input and generates a response to it.
        if not history:
            greeting_content_name = str(uuid.uuid4())
            greeting_start = json.dumps({
                "event": {
                    "contentStart": {
                        "promptName": self.nova_client.prompt_name,
                        "contentName": greeting_content_name,
                        "type": "TEXT",
                        "interactive": True,
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
                        "content": GREETING_TRIGGER_MARKER,
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

        # Open the single persistent audio stream LAST, after all context is sent.
        # Per AWS docs, this container stays open for the entire session.
        await self.nova_client.open_audio_stream()
        logger.info("Audio stream opened — ready for continuous streaming")


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
            # end_session() MUST be called before is_active=False.
            # end_session() has a `if not self.is_active: return` guard, so
            # calling it after would be a no-op and leave the AWS stream open
            # for ~55 s until Nova Sonic times it out from its side.
            await self.nova_client.end_session()
            self.nova_client.is_active = False
            self.nova_client = None
        self.active_connection = None

    async def receive_audio(self, audio_data: bytes):
        if self.nova_client and self.nova_client.is_active:
            try:
                await self.nova_client.send_audio_chunk(audio_data)
            except Exception as e:
                logger.error(f"Error sending audio chunk: {e}")

    async def _auto_reconnect(self) -> bool:
        """Transparently restart Nova Sonic session after a timeout.
        Replays conversation history so the AI has full context.
        Returns True if reconnect succeeded.
        """
        logger.info("Auto-reconnecting Nova Sonic session after timeout...")
        self._reconnect_done.clear()
        try:
            # Notify frontend of brief reconnect (non-fatal)
            if self.active_connection:
                try:
                    await self.active_connection.send_text(json.dumps({
                        "event": {"reconnecting": {"message": "Reconnecting..."}}
                    }))
                except Exception:
                    pass

            # Close old stream gracefully
            try:
                await self.nova_client.stream.input_stream.close()
            except Exception:
                pass

            # Create a fresh Nova Sonic client with the same interview context
            self.nova_client = InterviewNovaSonic(
                resume_text=self._resume_text,
                job_description=self._job_description,
                company_name=self._company_name,
                role_title=self._role_title,
                persona=self._persona,
                interview_type=self._interview_type,
                panelist_index=self._panel_index if self._interview_type == "panel" else 0,
                panel_total=len(self._panel_rotation) if self._interview_type == "panel" else 1,
                on_timeout=lambda: setattr(self, "_should_reconnect", True),
            )
            await self.nova_client.start_session()
            logger.info("New Nova Sonic session started")

            # Replay recent conversation history (trimmed to fit context limit)
            history = await self._load_history_from_db()
            if history:
                trimmed = history[-10:]  # Last 10 messages
                await self._replay_history(trimmed)

            # Inject greeting trigger (interactive=True) so AI resumes speaking
            # Without this, the AI sits silent waiting for user input after reconnect.
            kick_name = str(uuid.uuid4())
            await self.nova_client.send_event(json.dumps({
                "event": {"contentStart": {
                    "promptName": self.nova_client.prompt_name,
                    "contentName": kick_name,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "USER",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }}
            }))
            await self.nova_client.send_event(json.dumps({
                "event": {"textInput": {
                    "promptName": self.nova_client.prompt_name,
                    "contentName": kick_name,
                    "content": RECONNECT_TRIGGER_MARKER,
                }}
            }))
            await self.nova_client.send_event(json.dumps({
                "event": {"contentEnd": {
                    "promptName": self.nova_client.prompt_name,
                    "contentName": kick_name,
                }}
            }))

            # Re-open the persistent audio stream
            await self.nova_client.open_audio_stream()
            logger.info("Auto-reconnect complete — history replayed, resume trigger sent, audio stream open")

            # Clear accumulation buffers so old text from before the timeout
            # doesn't merge with the new agent's responses (prevents duplicate content)
            self.current_assistant_message = ""
            self.current_user_message = ""
            self.last_message_role = None

            # Notify frontend reconnect succeeded
            if self.active_connection:
                try:
                    await self.active_connection.send_text(json.dumps({
                        "event": {"reconnected": {"status": "ok"}}
                    }))
                except Exception:
                    pass

            self._should_reconnect = False
            return True

        except Exception as e:
            logger.error(f"Auto-reconnect failed: {e}")
            return False
        finally:
            self._reconnect_done.set()

    def _check_interview_complete(self, text: str) -> bool:
        """Return True if the AI's accumulated response contains a wrap-up phrase."""
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in _INTERVIEW_COMPLETE_PHRASES)

    async def _switch_panelist(self):
        """Switch to the next panelist in a panel interview.
        Closes the current Nova Sonic stream, creates a new one with the next
        panelist's persona and voice, replays conversation history, and notifies
        the frontend.
        """
        self._switching_panelist = True
        next_index = self._panel_index + 1
        next_panelist = self._panel_rotation[next_index]
        logger.info(f"Panel switch: {self._panel_rotation[self._panel_index]['name']} → {next_panelist['name']}")

        # Save any pending assistant message before switching
        if self.current_assistant_message.strip():
            await self.save_message("ASSISTANT", self.current_assistant_message.strip())
            self.add_history("ASSISTANT", self.current_assistant_message.strip())
            self.current_assistant_message = ""

        # Notify frontend of the upcoming switch
        if self.active_connection:
            try:
                await self.active_connection.send_text(json.dumps({
                    "event": {"panelistSwitching": {
                        "from": self._panel_rotation[self._panel_index]["name"],
                        "to": next_panelist["name"],
                        "role": next_panelist.get("role", "Interviewer"),
                    }}
                }))
            except Exception:
                pass

        # Properly shut down the old Nova Sonic session.
        # end_session() sets is_active=False and closes the stream, which stops
        # _process_responses from trying to read events from the dead stream.
        old_client = self.nova_client
        if old_client:
            await old_client.end_session()
            # Cancel the response processing task to prevent ValidationException
            if hasattr(old_client, 'response') and old_client.response and not old_client.response.done():
                old_client.response.cancel()
                try:
                    await old_client.response
                except (asyncio.CancelledError, Exception):
                    pass

        # Advance the rotation
        self._panel_index = next_index
        self._panel_user_responses = 0
        self._persona = next_panelist
        self._active_panelist = next_panelist["name"]
        self.interviewer_name = next_panelist["name"]

        # Create a new Nova Sonic client with the next panelist's persona + voice
        self.nova_client = InterviewNovaSonic(
            resume_text=self._resume_text,
            job_description=self._job_description,
            company_name=self._company_name,
            role_title=self._role_title,
            persona=next_panelist,
            interview_type="panel",
            panelist_index=self._panel_index,
            panel_total=len(self._panel_rotation),
            on_timeout=lambda: setattr(self, "_should_reconnect", True),
        )
        await self.nova_client.start_session()
        logger.info(f"New Nova Sonic session started for {next_panelist['name']} (voice: {next_panelist.get('voice', 'default')})")

        # Replay conversation history using the proper per-message pattern
        # (resume-conversation reference: individual contentStart/textInput/contentEnd per msg)
        history = await self._load_history_from_db()
        if history:
            trimmed = history[-10:]  # Last 10 messages — enough context without overwhelming
            await self._replay_history(trimmed)

        # Inject greeting trigger (interactive=True) so the new panelist starts speaking.
        # Per the speaks-first reference pattern, interactive=True makes Nova Sonic
        # treat this as real user input and generate a response.
        kick_content = str(uuid.uuid4())
        await self.nova_client.send_event(json.dumps({
            "event": {"contentStart": {
                "promptName": self.nova_client.prompt_name,
                "contentName": kick_content,
                "type": "TEXT",
                "interactive": True,
                "role": "USER",
                "textInputConfiguration": {"mediaType": "text/plain"},
            }}
        }))
        await self.nova_client.send_event(json.dumps({
            "event": {"textInput": {
                "promptName": self.nova_client.prompt_name,
                "contentName": kick_content,
                "content": GREETING_TRIGGER_MARKER,
            }}
        }))
        await self.nova_client.send_event(json.dumps({
            "event": {"contentEnd": {
                "promptName": self.nova_client.prompt_name,
                "contentName": kick_content,
            }}
        }))

        # Re-open the audio stream
        await self.nova_client.open_audio_stream()
        logger.info(f"Panel switch complete — {next_panelist['name']} is now active")

        # Notify frontend the switch is done
        if self.active_connection:
            try:
                await self.active_connection.send_text(json.dumps({
                    "event": {"panelistSwitched": {
                        "panelist": next_panelist["name"],
                        "role": next_panelist.get("role", "Interviewer"),
                        "index": self._panel_index,
                    }}
                }))
            except Exception:
                pass

        self._switching_panelist = False

    async def process_audio_responses(self):
        """Drain audio queue and send binary chunks to frontend. Restarts after reconnect."""
        while True:
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
                            # Sentinel pushed by _process_responses after the last
                            # audio chunk for an AI turn.  Because it goes through the
                            # same queue, it is guaranteed to arrive here only after
                            # every binary audio chunk has been sent to the frontend.
                            if audio_data == "__AI_AUDIO_DONE__":
                                try:
                                    done_event = json.dumps({"event": {"aiAudioDone": {}}})
                                    await self.active_connection.send_text(done_event)
                                except Exception:
                                    pass
                                continue

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

            # Inner loop exited. Wait for reconnect signal (max 20s) then re-enter.
            try:
                await asyncio.wait_for(self._reconnect_done.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                break
            if self.nova_client and self.nova_client.is_active:
                continue  # Resume with new client
            break


    async def process_events(self):
        """Drain event queue and forward to frontend. Auto-reconnects on timeout."""
        while True:
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

                            # On barge-in: discard the partial message and clear the
                            # buffer. Do NOT save it — the AI will restart and deliver
                            # the complete response, which gets saved on the next role
                            # change. Saving the partial here creates duplicate DB rows
                            # that confuse the AI when history is replayed on reconnect.
                            if "event" in event_data and "bargeIn" in event_data["event"]:
                                self.current_assistant_message = ""

                            if "event" in event_data and "textOutput" in event_data["event"]:
                                # We received text from the AI - reset circuit breaker
                                self._consecutive_reconnect_failures = 0
                                text_content = event_data["event"]["textOutput"].get("content", "")
                                
                                # If this text block is just the raw barge-in interrupt signal,
                                # immediately discard it and flush the aborted buffer so it isn't saved.
                                if '"interrupted":' in text_content and 'true' in text_content.lower():
                                    self.current_assistant_message = ""
                                    continue

                                role = event_data["event"]["textOutput"].get("role", "ASSISTANT")

                                # Detect role change - save accumulated message from previous speaker
                                if self.last_message_role and self.last_message_role != role:
                                    if self.last_message_role == "USER" and self.current_user_message.strip():
                                        await self.save_message("USER", self.current_user_message.strip())
                                        self.add_history("USER", self.current_user_message.strip())
                                        self.current_user_message = ""

                                        # Panel mode: count user responses and trigger panelist switch
                                        if (self._interview_type == "panel"
                                                and not self._switching_panelist
                                                and self._panel_rotation):
                                            self._panel_user_responses += 1
                                            if (self._panel_user_responses >= self._questions_per_panelist
                                                    and self._panel_index < len(self._panel_rotation) - 1):
                                                await self._switch_panelist()
                                                continue

                                    elif self.last_message_role == "ASSISTANT" and self.current_assistant_message.strip():
                                        await self.save_message("ASSISTANT", self.current_assistant_message.strip())
                                        self.add_history("ASSISTANT", self.current_assistant_message.strip())
                                        self.current_assistant_message = ""

                                # Accumulate current chunk — skip the greeting trigger sentinel
                                if role == "USER":
                                    if GREETING_TRIGGER_MARKER not in text_content and RECONNECT_TRIGGER_MARKER not in text_content:
                                        self.current_user_message += text_content
                                else:  # ASSISTANT
                                    self.current_assistant_message += text_content

                                    # Panel mode: tag every assistant event with the active panelist
                                    if self._interview_type == "panel" and self._active_panelist:
                                        event_data["event"]["textOutput"]["panelist"] = self._active_panelist
                                        event_json = json.dumps(event_data)

                                    # Detect interview completion phrases in AI responses
                                    if self._check_interview_complete(self.current_assistant_message):
                                        complete_event = json.dumps({
                                            "event": {"interviewComplete": {"message": "The interview has concluded."}}
                                        })
                                        try:
                                            await self.active_connection.send_text(complete_event)
                                        except Exception:
                                            pass

                                # Update last role
                                self.last_message_role = role

                            # Check if this is a timeout error event — trigger reconnect
                            if (
                                "event" in event_data
                                and "error" in event_data["event"]
                                and event_data["event"]["error"].get("code") == "MODEL_TIMEOUT"
                            ):
                                logger.info("MODEL_TIMEOUT detected — triggering auto-reconnect")
                                # Save active buffers to DB so the previous AI response isn't lost/repeated on reconnect
                                if self.current_user_message.strip():
                                    await self.save_message("USER", self.current_user_message.strip())
                                    self.add_history("USER", self.current_user_message.strip())
                                    self.current_user_message = ""
                                if self.current_assistant_message.strip():
                                    await self.save_message("ASSISTANT", self.current_assistant_message.strip())
                                    self.add_history("ASSISTANT", self.current_assistant_message.strip())
                                    self.current_assistant_message = ""
                                
                                self._consecutive_reconnect_failures += 1
                                if self._consecutive_reconnect_failures > 2:
                                    logger.error("Auto-reconnect circuit breaker tripped (3 consecutive failures).")
                                    # Fall through to standard error handling to end session cleanly
                                    error_event = json.dumps({
                                        "event": {
                                            "error": {
                                                "code": "SESSION_ERROR",
                                                "message": "The connection to the AI was lost permanently. Please refresh and try again.",
                                            }
                                        }
                                    })
                                    try:
                                        await self.active_connection.send_text(error_event)
                                    except Exception:
                                        pass
                                    self._should_reconnect = False
                                else:
                                    self._should_reconnect = True
                                break  # Break inner loop to trigger reconnect / shutdown

                            await self.active_connection.send_text(event_json)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"Error processing event: {e}")
                        await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"Event loop error: {e}")

            # Inner loop exited (is_active=False or exception).
            # Drain any remaining events to catch MODEL_TIMEOUT that arrived just as
            # is_active was set to False (fixes the race condition).
            if self.nova_client:
                try:
                    while not self.nova_client.event_queue.empty():
                        remaining = self.nova_client.event_queue.get_nowait()
                        remaining_data = json.loads(remaining)
                        if (
                            "event" in remaining_data
                            and "error" in remaining_data["event"]
                            and remaining_data["event"]["error"].get("code") == "MODEL_TIMEOUT"
                        ):
                            logger.info("MODEL_TIMEOUT found in queue drain — triggering auto-reconnect")
                            # Save active buffers to DB so the previous AI response isn't lost/repeated on reconnect
                            if self.current_user_message.strip():
                                await self.save_message("USER", self.current_user_message.strip())
                                self.add_history("USER", self.current_user_message.strip())
                                self.current_user_message = ""
                            if self.current_assistant_message.strip():
                                await self.save_message("ASSISTANT", self.current_assistant_message.strip())
                                self.add_history("ASSISTANT", self.current_assistant_message.strip())
                                self.current_assistant_message = ""
                            self._consecutive_reconnect_failures += 1
                            if self._consecutive_reconnect_failures <= 2:
                                self._should_reconnect = True
                except Exception:
                    pass

            # Reconnect if flagged (set either by on_timeout callback or queue drain above).
            if self._should_reconnect:
                success = await self._auto_reconnect()
                if success:
                    continue  # Re-enter outer loop with new nova_client
            break


# Active interview managers
active_interviews: dict[str, InterviewConnectionManager] = {}


def verify_ws_token(token: str) -> str | None:
    """Verify JWT token from WebSocket query param."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


MAX_CONCURRENT_SESSIONS_PER_USER = 2

@app.websocket("/ws/interview/{session_id}")
async def interview_websocket(websocket: WebSocket, session_id: str, token: str = Query("")):
    # Authenticate
    user_id = verify_ws_token(token)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Rate limit: prevent a single user from opening many concurrent AI sessions
    user_active_count = sum(1 for mgr in active_interviews.values() if mgr.user_id == user_id)
    if user_active_count >= MAX_CONCURRENT_SESSIONS_PER_USER:
        await websocket.close(code=4029, reason="Too many active sessions")
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
    init_payload = {
        "sessionId": session_id,
        "status": "connected",
        "interviewerName": manager.interviewer_name,
        "interviewType": manager._interview_type,
    }
    if manager._interview_type == "panel":
        init_payload["panelists"] = [
            {"name": p["name"], "role": p["role"], "style": p["style"]}
            for p in PANEL_PERSONAS
        ]
    await websocket.send_text(json.dumps({
        "event": {
            "init": init_payload
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
                    if data.get("type") == "code_submission":
                        # ── Handle code shared from the coding editor ──
                        code = data.get("code", "")
                        lang = data.get("language", "python")
                        if code.strip() and manager.nova_client and manager.nova_client.is_active:
                            # Save any pending assistant message first
                            if manager.current_assistant_message.strip():
                                await manager.save_message("ASSISTANT", manager.current_assistant_message.strip())
                                manager.add_history("ASSISTANT", manager.current_assistant_message.strip())
                                manager.current_assistant_message = ""

                            # Save code as a CODE message in the transcript
                            code_text = f"```{lang}\n{code}\n```"
                            await manager.save_message("CODE", code_text)
                            manager.add_history("USER", f"[Code submission in {lang}]:\n{code_text}")

                            # Truncate very large submissions to avoid overwhelming Nova Sonic
                            truncated_code = code[:2000] + ("\n# ... (truncated)" if len(code) > 2000 else "")

                            # Close the audio stream first — Nova Sonic can't have two
                            # interactive content blocks open simultaneously
                            await manager.nova_client.close_audio_stream()
                            await asyncio.sleep(0.05)  # Brief settle

                            # Inject code into Nova Sonic as interactive text so the AI reviews it
                            code_content_name = str(uuid.uuid4())
                            code_prompt = f"The candidate has submitted the following {lang} code for your review:\n```{lang}\n{truncated_code}\n```\nPlease review it and provide feedback."
                            await manager.nova_client.send_event(json.dumps({
                                "event": {"contentStart": {
                                    "promptName": manager.nova_client.prompt_name,
                                    "contentName": code_content_name,
                                    "type": "TEXT",
                                    "interactive": True,
                                    "role": "USER",
                                    "textInputConfiguration": {"mediaType": "text/plain"},
                                }}
                            }))
                            await manager.nova_client.send_event(json.dumps({
                                "event": {"textInput": {
                                    "promptName": manager.nova_client.prompt_name,
                                    "contentName": code_content_name,
                                    "content": code_prompt,
                                }}
                            }))
                            await manager.nova_client.send_event(json.dumps({
                                "event": {"contentEnd": {
                                    "promptName": manager.nova_client.prompt_name,
                                    "contentName": code_content_name,
                                }}
                            }))

                            # Reopen the audio stream so mic input resumes
                            await manager.nova_client.open_audio_stream()

                            manager.last_message_role = "USER"
                            logger.info(f"Code submission injected into Nova Sonic ({lang}, {len(code)} chars)")
                    elif "event" in data:
                        event = data["event"]
                        if "textInput" in event:
                            user_text = event["textInput"].get("content", "")
                            if manager.last_message_role and manager.last_message_role != "USER":
                                if manager.current_assistant_message.strip():
                                    await manager.save_message("ASSISTANT", manager.current_assistant_message.strip())
                                    manager.add_history("ASSISTANT", manager.current_assistant_message.strip())
                                    manager.current_assistant_message = ""
                            manager.current_user_message += user_text
                            manager.last_message_role = "USER"
                    elif message["text"] == "end_interview":
                        break
                except json.JSONDecodeError:
                    if message["text"] == "end_interview":
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

import os
import asyncio
import base64
import json
import uuid
import random
import boto3
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient, InvokeModelWithBidirectionalStreamOperationInput
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk, BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.config import Config, HTTPAuthSchemeResolver, SigV4AuthScheme
from smithy_aws_core.credentials_resolvers.environment import EnvironmentCredentialsResolver
from smithy_aws_core.credentials_resolvers.static import StaticCredentialsResolver
from smithy_aws_core.identity import AWSCredentialsIdentity

# Audio configuration
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SIZE = 4096

# ─── Interviewer Personas ────────────────────────────────────
# Each persona has a name, Nova Sonic voiceId, gender, and personality style.
# Voices are gender-matched to names.
INTERVIEWER_PERSONAS = [
    # Masculine voices
    {"name": "James",  "voice": "matthew",  "gender": "male",   "style": "warm and encouraging"},
    {"name": "Marcus", "voice": "matthew",  "gender": "male",   "style": "direct and analytical"},
    {"name": "Daniel", "voice": "matthew",  "gender": "male",   "style": "casual and conversational"},
    {"name": "Raj",    "voice": "arjun",    "gender": "male",   "style": "thoughtful and methodical"},
    {"name": "David",  "voice": "matthew",  "gender": "male",   "style": "friendly and approachable"},
    # Feminine voices
    {"name": "Sarah",  "voice": "tiffany",  "gender": "female", "style": "professional and structured"},
    {"name": "Emily",  "voice": "tiffany",  "gender": "female", "style": "warm and supportive"},
    {"name": "Priya",  "voice": "kiara",    "gender": "female", "style": "insightful and engaging"},
    {"name": "Rachel", "voice": "amy",      "gender": "female", "style": "direct and efficient"},
    {"name": "Olivia", "voice": "olivia",   "gender": "female", "style": "friendly and encouraging"},
]


def pick_random_persona() -> dict:
    """Select a random interviewer persona."""
    return random.choice(INTERVIEWER_PERSONAS)


def get_aws_credentials_resolver():
    """Get AWS credentials using boto3 credential chain."""
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials:
            aws_credentials = AWSCredentialsIdentity(
                access_key_id=credentials.access_key,
                secret_access_key=credentials.secret_key,
                session_token=credentials.token,
            )
            return StaticCredentialsResolver(credentials=aws_credentials)
        else:
            return EnvironmentCredentialsResolver()
    except Exception as e:
        print(f"Warning: Could not resolve AWS credentials via boto3: {e}")
        return EnvironmentCredentialsResolver()


class InterviewNovaSonic:
    """Nova Sonic client adapted for interview sessions."""

    def __init__(
        self,
        resume_text: str = "",
        job_description: str = "",
        company_name: str = "",
        role_title: str = "",
        persona: dict | None = None,
        model_id: str = "amazon.nova-2-sonic-v1:0",
        region: str = "us-east-1",
        on_timeout: callable = None,
    ):
        self.model_id = model_id
        self.region = region
        self.client = None
        self.stream = None
        self.response = None
        self.is_active = False
        self.prompt_name = str(uuid.uuid4())
        self.content_name = str(uuid.uuid4())
        self.audio_content_name = str(uuid.uuid4())
        self.audio_queue = asyncio.Queue()
        self.event_queue = asyncio.Queue()
        self.role = None
        self.display_assistant_text = False
        self.barge_in = False
        self.resume_text = resume_text
        self.job_description = job_description
        self.company_name = company_name
        self.role_title = role_title
        # Persona: randomly assigned if not provided
        self.persona = persona or pick_random_persona()
        # Called synchronously when a MODEL_TIMEOUT is detected, before is_active=False
        self.on_timeout = on_timeout

    def _initialize_client(self):
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=get_aws_credentials_resolver(),
            http_auth_scheme_resolver=HTTPAuthSchemeResolver(),
            http_auth_schemes={"aws.auth#sigv4": SigV4AuthScheme()},
        )
        self.client = BedrockRuntimeClient(config=config)

    async def send_event(self, event_json):
        event = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
        )
        await self.stream.input_stream.send(event)

    def _build_system_prompt(self) -> str:
        """Build a dynamic, natural-sounding system prompt based on the assigned persona."""
        name = self.persona["name"]
        style = self.persona["style"]
        role_info = self.role_title or "the position"
        company_info = f" at {self.company_name}" if self.company_name else ""

        # Pick a random greeting template so the opening line varies each time
        greetings = [
            f"Hey, thanks for joining! I'm {name}, I'll be your interviewer today.",
            f"Hi! My name's {name}. Great to meet you — let's get started.",
            f"Hello! I'm {name}. Thanks for taking the time to chat with me today.",
            f"Hey there, I'm {name}. Excited to learn more about you — ready to jump in?",
            f"Hi, welcome! I'm {name}, and I'll be walking you through this interview.",
            f"Good to meet you! I'm {name}. Let's have a conversation about your background.",
        ]
        greeting = random.choice(greetings)

        # Pick a random icebreaker so the first question varies too
        icebreakers = [
            "Tell me a bit about yourself and your background.",
            "I'd love to hear your story — what's your professional background?",
            "Before we dive in, give me a quick overview of where you're at in your career.",
            "Let's start easy — walk me through your professional journey so far.",
            "To kick things off, tell me what brought you to this field.",
        ]
        icebreaker = random.choice(icebreakers)

        prompt = f"""You are {name}, an experienced interviewer conducting a practice interview for {role_info}{company_info}.

Your personality is {style}. Let that come through naturally in how you speak — don't be robotic or overly formal. Talk like a real person having a professional conversation.

RULES:

1. OPENING:
   - Greet the candidate naturally: "{greeting}"
   - Start with this icebreaker: "{icebreaker}"

2. INTERVIEW FLOW:
   - Ask 5-8 questions total, progressing from general to more specific
   - Mix behavioral, technical, and situational questions relevant to the role
   - Ask follow-up questions when answers are vague or interesting
   - Transition between questions naturally — acknowledge what they said, react briefly, then move on
   - Wrap up professionally when done

3. HOW TO SPEAK:
   - Be conversational — use natural transitions like "That's interesting", "Got it", "Nice", "Makes sense"
   - Keep responses SHORT (1-3 sentences before the next question)
   - Vary your reactions — don't repeat the same phrases
   - Don't give feedback, scores, or coaching tips during the interview
   - If they give a great answer, you can briefly acknowledge it before moving on
   - If they struggle, gently rephrase or offer a related angle

4. CONTEXT:
   - Job Description: {self.job_description}
   - Candidate Resume: {self.resume_text if self.resume_text else 'Not provided'}

5. QUESTION MIX:
   - Behavioral ("Tell me about a time when...")
   - Technical (relevant to the job description)
   - Situational / hypothetical scenarios
   - Resume-specific questions about their past experience

6. IMPORTANT:
   - Stay in character as {name} throughout — you are a human interviewer, not an AI
   - Never break character, mention AI, or provide tips
   - Sound natural, not scripted — vary your word choice and phrasing
   - When wrapping up, thank the candidate warmly and let them know the interview is complete"""

        return prompt

    async def start_session(self):
        """Start a new interview session."""
        if not self.client:
            self._initialize_client()

        self.stream = await self.client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
        )
        self.is_active = True

        # Session start — with server-side turn detection so Nova Sonic
        # handles endpointing instead of fragile client-side VAD.
        session_start = json.dumps({
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 1024,
                        "topP": 0.9,
                        "temperature": 0.7,
                    },
                    "turnDetectionConfiguration": {
                        "endpointingSensitivity": "MEDIUM"
                    }
                }
            }
        })
        await self.send_event(session_start)

        # Prompt start (no tools needed for interview)
        prompt_start = json.dumps({
            "event": {
                "promptStart": {
                    "promptName": self.prompt_name,
                    "textOutputConfiguration": {"mediaType": "text/plain"},
                    "audioOutputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 24000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "voiceId": self.persona["voice"],
                        "encoding": "base64",
                        "audioType": "SPEECH",
                    },
                }
            }
        })
        await self.send_event(prompt_start)

        # System prompt
        content_start = json.dumps({
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "SYSTEM",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        })
        await self.send_event(content_start)

        system_prompt = self._build_system_prompt()
        text_input = json.dumps({
            "event": {
                "textInput": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name,
                    "content": system_prompt,
                }
            }
        })
        await self.send_event(text_input)

        content_end = json.dumps({
            "event": {
                "contentEnd": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name,
                }
            }
        })
        await self.send_event(content_end)

        # Start processing responses
        self.response = asyncio.create_task(self._process_responses())

    async def open_audio_stream(self):
        """Open the single persistent audio content container.

        Per AWS docs, all audio frames share ONE container for the entire
        session.  Call this once after all context (system prompt, history,
        greeting trigger) has been sent.  Audio is then streamed continuously
        via send_audio_chunk until the session ends.
        """
        audio_content_start = json.dumps({
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": self.audio_content_name,
                    "type": "AUDIO",
                    "interactive": True,
                    "role": "USER",
                    "audioInputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 16000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "audioType": "SPEECH",
                        "encoding": "base64",
                    },
                }
            }
        })
        await self.send_event(audio_content_start)

    async def send_audio_chunk(self, audio_bytes):
        if not self.is_active:
            return
        blob = base64.b64encode(audio_bytes)
        audio_event = json.dumps({
            "event": {
                "audioInput": {
                    "promptName": self.prompt_name,
                    "contentName": self.audio_content_name,
                    "content": blob.decode("utf-8"),
                }
            }
        })
        await self.send_event(audio_event)

    async def end_session(self):
        """Properly close audio stream → prompt → session in the correct order."""
        if not self.is_active:
            return
        try:
            # 1. Close the audio content container
            audio_content_end = json.dumps({
                "event": {
                    "contentEnd": {
                        "promptName": self.prompt_name,
                        "contentName": self.audio_content_name,
                    }
                }
            })
            await self.send_event(audio_content_end)
        except Exception:
            pass
        try:
            # 2. End the prompt
            prompt_end = json.dumps({
                "event": {"promptEnd": {"promptName": self.prompt_name}}
            })
            await self.send_event(prompt_end)
        except Exception:
            pass
        try:
            # 3. End the session
            session_end = json.dumps({"event": {"sessionEnd": {}}})
            await self.send_event(session_end)
        except Exception:
            pass
        try:
            await self.stream.input_stream.close()
        except Exception:
            pass

    async def _process_responses(self):
        """Process responses from Nova Sonic."""
        try:
            while self.is_active:
                output = await self.stream.await_output()
                result = await output[1].receive()

                if result.value and result.value.bytes_:
                    response_data = result.value.bytes_.decode("utf-8")
                    json_data = json.loads(response_data)

                    if "event" in json_data:
                        if "contentStart" in json_data["event"]:
                            await self.event_queue.put(json.dumps(json_data))
                            content_start = json_data["event"]["contentStart"]
                            self.role = content_start.get("role")
                            # Reset barge-in when Nova Sonic starts a fresh ASSISTANT response.
                            # Without this, barge_in stays True forever and all audio is silenced.
                            if self.role == "ASSISTANT":
                                self.barge_in = False
                            if "additionalModelFields" in content_start:
                                additional_fields = json.loads(
                                    content_start["additionalModelFields"]
                                )
                                self.display_assistant_text = (
                                    additional_fields.get("generationStage") == "SPECULATIVE"
                                )
                            else:
                                self.display_assistant_text = False

                        elif "textOutput" in json_data["event"]:
                            text = json_data["event"]["textOutput"]["content"]
                            # Detect barge-in BEFORE forwarding to frontend so the raw
                            # '{ "interrupted": true }' string is never queued or saved.
                            is_barge_in = False
                            try:
                                parsed_text = json.loads(text)
                                if isinstance(parsed_text, dict) and parsed_text.get("interrupted"):
                                    is_barge_in = True
                            except (json.JSONDecodeError, ValueError):
                                # Fallback for legacy string format
                                if "interrupted" in text and "true" in text:
                                    is_barge_in = True

                            if is_barge_in:
                                print("Barge-in detected")
                                self.barge_in = True
                                barge_in_event = {
                                    "event": {
                                        "bargeIn": {
                                            "status": "interrupted"
                                        }
                                    }
                                }
                                await self.event_queue.put(json.dumps(barge_in_event))
                                continue

                            # Normal text — forward to frontend
                            await self.event_queue.put(json.dumps(json_data))
                            if self.role == "ASSISTANT" and self.display_assistant_text:
                                print(f"Interviewer: {text}")
                            elif self.role == "USER":
                                print(f"Candidate: {text}")

                        elif "audioOutput" in json_data["event"]:
                            if not self.barge_in:
                                audio_content = json_data["event"]["audioOutput"]["content"]
                                audio_bytes = base64.b64decode(audio_content)
                                await self.audio_queue.put(audio_bytes)

                        else:
                            # Forward all other events (contentEnd, etc.) to frontend
                            await self.event_queue.put(json.dumps(json_data))

                            # When the AI finishes an audio response, push a sentinel
                            # through the AUDIO queue so it arrives at the frontend
                            # strictly AFTER every audio chunk.  process_audio_responses
                            # converts this into an aiAudioDone text event.
                            if "contentEnd" in json_data["event"]:
                                ct = json_data["event"]["contentEnd"]
                                if ct.get("type") == "AUDIO":
                                    await self.audio_queue.put("__AI_AUDIO_DONE__")

        except Exception as e:
            from aws_sdk_bedrock_runtime.models import ModelTimeoutException, ValidationException

            # Recoverable errors that should trigger transparent auto-reconnect:
            # - ModelTimeoutException (session idle too long)
            # - "Timed out waiting for audio bytes" (audio stream stall)
            # - "Chat history is over max limit" (too many replay messages)
            is_timeout = isinstance(e, ModelTimeoutException) or (
                isinstance(e, ValidationException)
                and ("Timed out" in str(e) or "over max limit" in str(e))
            )

            if is_timeout:
                print(f"Nova Sonic session timed out (will reconnect): {e}")
                # Signal the manager to reconnect BEFORE setting is_active=False so
                # process_events doesn't exit its inner loop before seeing this flag.
                if self.on_timeout:
                    self.on_timeout()
                timeout_event = json.dumps({
                    "event": {
                        "error": {
                            "code": "MODEL_TIMEOUT",
                            "message": "Reconnecting session after timeout…",
                        }
                    }
                })
                await self.event_queue.put(timeout_event)
            else:
                print(f"Error processing responses: {e}")
                if hasattr(e, "__traceback__"):
                    import traceback
                    traceback.print_exc()
                # Push a generic error so the frontend isn't left hanging
                error_event = json.dumps({
                    "event": {
                        "error": {
                            "code": "SESSION_ERROR",
                            "message": "The interview session encountered an error. Please start a new session.",
                        }
                    }
                })
                await self.event_queue.put(error_event)
            # Cleanly shut down all loops
            self.is_active = False


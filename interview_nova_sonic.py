import os
import asyncio
import base64
import json
import uuid
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
CHUNK_SIZE = 512


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
        model_id: str = "amazon.nova-sonic-v1:0",
        region: str = "us-east-1",
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
        """Build the system prompt for the interview session."""
        role_info = self.role_title or "the position"
        company_info = f" at {self.company_name}" if self.company_name else ""

        prompt = f"""You are Alex, a professional and experienced interviewer conducting a practice interview for {role_info}{company_info}. 

Your job is to simulate a realistic interview experience. Follow these rules strictly:

1. INTERVIEW STRUCTURE:
   - Start with a warm greeting: "Hi there! I'm Alex, and I'll be conducting your interview today."
   - Begin with a simple icebreaker question (e.g., "Tell me about yourself")
   - Progress through behavioral, situational, and technical questions relevant to the role
   - Ask 5-8 questions total during the interview
   - After the last question, wrap up the interview professionally

2. INTERVIEW STYLE:
   - Be professional but friendly, like a real interviewer
   - Listen carefully to responses and ask relevant follow-up questions
   - Keep your responses short and conversational (2-3 sentences max)
   - Don't give feedback or coaching during the interview - save that for after
   - If the candidate gives a vague answer, probe deeper with follow-up questions

3. CONTEXT:
   - Job Description: {self.job_description}
   - Candidate Resume: {self.resume_text if self.resume_text else 'Not provided'}
   
4. QUESTION TYPES TO INCLUDE:
   - Behavioral questions (STAR format expected)
   - Technical questions relevant to the job description
   - Situational/hypothetical questions
   - Questions about the candidate's experience from their resume

5. IMPORTANT:
   - Stay in character as an interviewer throughout
   - Do NOT break character or provide tips during the interview
   - React naturally to answers before moving to the next question
   - When wrapping up, thank the candidate and let them know the interview is complete"""

        return prompt

    async def start_session(self):
        """Start a new interview session."""
        if not self.client:
            self._initialize_client()

        self.stream = await self.client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
        )
        self.is_active = True

        # Session start
        session_start = json.dumps({
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 1024,
                        "topP": 0.9,
                        "temperature": 0.7,
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
                        "voiceId": "matthew",
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

    async def start_audio_input(self):
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

    async def end_audio_input(self):
        audio_content_end = json.dumps({
            "event": {
                "contentEnd": {
                    "promptName": self.prompt_name,
                    "contentName": self.audio_content_name,
                }
            }
        })
        await self.send_event(audio_content_end)

    async def end_session(self):
        if not self.is_active:
            return
        prompt_end = json.dumps({
            "event": {"promptEnd": {"promptName": self.prompt_name}}
        })
        await self.send_event(prompt_end)
        session_end = json.dumps({"event": {"sessionEnd": {}}})
        await self.send_event(session_end)
        await self.stream.input_stream.close()

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
                        await self.event_queue.put(json.dumps(json_data))

                        if "contentStart" in json_data["event"]:
                            content_start = json_data["event"]["contentStart"]
                            self.role = content_start.get("role")
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
                            if '{ "interrupted" : true }' in text:
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

                            if self.role == "ASSISTANT" and self.display_assistant_text:
                                print(f"Interviewer: {text}")
                            elif self.role == "USER":
                                print(f"Candidate: {text}")

                        elif "audioOutput" in json_data["event"]:
                            if not self.barge_in:
                                audio_content = json_data["event"]["audioOutput"]["content"]
                                audio_bytes = base64.b64decode(audio_content)
                                await self.audio_queue.put(audio_bytes)

        except Exception as e:
            print(f"Error processing responses: {e}")
            if hasattr(e, "__traceback__"):
                import traceback
                traceback.print_exc()

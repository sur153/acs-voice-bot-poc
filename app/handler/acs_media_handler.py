"""Handles media streaming to Azure Voice Live API via WebSocket."""

import asyncio
import base64
from datetime import datetime, timezone
import json
import logging
import os
import sys
import uuid
import urllib.parse
from azure.identity.aio import ManagedIdentityCredential
import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Data
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import AzureCliCredential, DefaultAzureCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.communication.callautomation.aio import CallAutomationClient
from azure.communication.callautomation import PhoneNumberIdentifier, CommunicationUserIdentifier
from app.handler.acs_cosmos_client import get_container, update_session_transcript
from app.handler.acs_event_handler import AcsEventHandler
from app.handler.transfer_handler import TransferHandler
logger = logging.getLogger(__name__)

def session_config():
    """Returns the default session configuration for Voice Live.

    LATENCY OPTIMIZED: Balanced for speed without cutting off words.
    Original values preserved in comments for rollback if needed.
    """
    return {
        "type": "session.update",
        "session": {
            # Explicit audio format to ensure consistency
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.5,           # Match official SDK
                "prefix_padding_ms": 300,   # Match official SDK (was 200)
                "silence_duration_ms": 500, # Match official SDK (was 900) - shorter for responsiveness
                "remove_filler_words": False, # Keep disabled
                "end_of_utterance_detection": {
                    "model": "semantic_detection_v1",
                    "threshold": 0.4,
                    "timeout": 2.0,         # Slightly reduced for faster response
                },
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "en"
            },
            "voice": {
                "name": "en-US-AvaNeural",
                "type": "azure-standard",
                "temperature": 0.3,
            }
        },
    }

class ACSMediaHandler:
    """Manages audio streaming between client and Azure Voice Live API."""

    def __init__(self, config):
        self.endpoint = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model = config["VOICE_LIVE_MODEL"]
        self.api_key = config["AZURE_VOICE_LIVE_API_KEY"]
        self.foundry_project_name = config["AZURE_VOICELIVE_PROJECT_NAME"]

        self.agent_id =  config["AZURE_VOICELIVE_AGENT_ID"] #manager.CreateAgent()
        self.agent_name = config["AZURE_VOICELIVE_AGENT_NAME"]

        self.client_id = config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"]
        self.send_queue = asyncio.Queue()
        self.ws = None
        self.send_task = None
        self.incoming_websocket = None
        self.is_raw_audio = True
        self._last_transcript = None
        self._response_in_progress = False  # Track if agent is currently responding
        self._session_ready = False  # Track if Voice Live session is ready
        self._conversation_history = []  # Track conversation for transfer context
        self._current_session_id = None  # Current Voice Live session ID
        self._phone_number = None  # Caller's phone number
        self._transfer_handler = TransferHandler(config)  # Transfer orchestration

        # Text delta throttling to prevent audio jitter
        # Accumulates text deltas and sends them in batches to reduce WebSocket traffic
        self._pending_transcript_delta = ""
        self._transcript_delta_task = None
        self._transcript_throttle_interval = 0.2  # Send text updates every 200ms max

        # Audio batching - use smaller chunks for smoother playback
        # Official SDK uses 50ms chunks (1200 samples = 2400 bytes)
        # Smaller chunks = lower latency but more messages
        self._audio_buffer = bytearray()
        self._audio_batch_size = 2400  # ~50ms of audio at 24kHz (matching official SDK)
        self._audio_flush_task = None
        self._audio_flush_interval = 0.04  # Flush every 40ms max

        # ACS client for making outbound calls to agent
        self._acs_connection_string = config.get("ACS_CONNECTION_STRING", "")
        self._acs_callback_url = config.get("ACS_DEV_TUNNEL", "")
        self._acs_source_phone = config.get("ACS_SOURCE_PHONE_NUMBER", "")  # Your purchased ACS phone number
        self._cognitive_services_endpoint = config.get("AZURE_VOICE_LIVE_ENDPOINT", "")  # For TTS
        self._acs_client = None
        if self._acs_connection_string:
            self._acs_client = CallAutomationClient.from_connection_string(
                self._acs_connection_string
            )

    def _generate_guid(self):
        return str(uuid.uuid4())

    async def connect(self):
        """Connects to Azure Voice Live API via WebSocket."""
        endpoint = self.endpoint.rstrip("/")
        model = self.model.strip()
        agent_name = self.agent_name.strip()
        project_name = self.foundry_project_name.strip()
        print(f"Created agent with ID: {agent_name}")

        # agent_access_token = (await DefaultAzureCredential().get_token("https://ai.azure.com/.default")).token
        
        # logger.info("Obtained agent access token")
        # url = f"{endpoint}/voice-live/realtime?api-version=2025-05-01-preview&model={model}"
        # url = f"{endpoint}/voice-live/realtime?api-version=2025-10-01-preview&agent-id={agent_id}&agent-project-name={project_id}"
        # url = f"https://surbhi-resource.cognitiveservices.azure.com/voice-live/realtime?api-version=2025-10-01&agent_id=asst_pMNJiqPYtGiXllCvKRIevbHf&project_id=surbhi"

        # url = f"https://oaiwtw.cognitiveservices.azure.com/voice-live/realtime?api-version=2025-10-01&agent_id=asst_BK6pub3mnRqZ0ICib1osVDEC&project_id=76a2ae5a-9f00-4f6b-95ed-5d33d77c4d61&agent-access-token={agent_access_token}"
        # url = f"{endpoint}/voice-live/realtime?api-version=2025-05-01-preview&agent_id=asst_pMNJiqPYtGiXllCvKRIevbHf&project_id=surbhi&api-key=${urllib.parse.quote(self.api_key)}"
        url = f"{endpoint}/voice-live/realtime?api-version=2025-10-01&x-ms-client-request-id={self._generate_guid()}&agent_name={agent_name}&agent-project-name={project_name}"

      
        # url = f"https://v-foundryh2da.cognitiveservices.azure.com/voice-agent/realtime?api-version=2025-10-01&x-ms-client-request-id=48e60f07-2308-4768-9870-93004fe88850&agent-name=my-voic-agent&agent-project-name=voice-bot-projecth2da"
        url = url.replace("https://", "wss://")
        # headers = {"x-ms-client-request-id": self._generate_guid()}
        # headers["Authorization"] = f"Bearer {agent_access_token}"
        # headers = {"Authorization": f"Bearer {agent_access_token}"}

        # Use API key if available, otherwise try Azure credentials
        # if self.api_key:
        #     headers = {"api-key": self.api_key}
        #     logger.info("[VoiceLiveACSHandler] Using API key authentication")
        if self.client_id:
            # Use async context manager to auto-close the credential
            async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                token = await credential.get_token(
                    "https://ai.azure.com/.default"
                )
                logger.info("[VoiceLiveACSHandler] Using managed identity authentication")
                headers = {"Authorization": f"Bearer {token.token}"}
        else:
            agent_access_token = (await DefaultAzureCredential().get_token("https://ai.azure.com/.default")).token
            headers = {"Authorization": f"Bearer {agent_access_token}"}
            logger.info("[VoiceLiveACSHandler] Using DefaultAzureCredential authentication")
        # Add ping_interval and ping_timeout to prevent timeout errors

        logger.info("[VoiceLiveACSHandler] Connecting to URL: %s", url)
        logger.info("[VoiceLiveACSHandler] Using headers: %s", {k: v[:20] + '...' if len(str(v)) > 20 else v for k, v in headers.items()})

        try:
            self.ws = await ws_connect(
                url,
                additional_headers=headers,
                ping_interval=30,  # Send a ping every 30 seconds
                ping_timeout=60,   # Wait up to 60 seconds for a pong response
            )
            logger.info("[VoiceLiveACSHandler] WebSocket connected successfully to agent: %s", self.agent_name or "default")
        except Exception as e:
            logger.error("[VoiceLiveACSHandler] Failed to connect to Voice Live API: %s", e)
            raise
       
        await self._send_json(session_config())
        # await self.ws.send_json({
        #     "type": "response.create",
        #     "response": {
        #         "instructions": "Hello! How can I assist you with your insurance needs today?"
        #     }
        # })
        # await self._send_json({"type": "response.create"})

        asyncio.create_task(self._receiver_loop())
        self.send_task = asyncio.create_task(self._sender_loop())

    async def init_incoming_websocket(self, socket, is_raw_audio=True):
        """Sets up incoming ACS WebSocket."""
        self.incoming_websocket = socket
        self.is_raw_audio = is_raw_audio

    async def audio_to_voicelive(self, audio_b64: str):
        """Queues audio data to be sent to Voice Live API."""
        await self.send_queue.put(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
        )
    
    async def saveToDatabase(self, phone_number, session_id):
        """Save session to Cosmos DB asynchronously to avoid blocking the event loop."""
        def _sync_save():
            container = get_container()
            item = {
                "id": f"session-{session_id}",
                "meta": {
                    "session_id": session_id,
                    "interview_date": datetime.now(timezone.utc).isoformat(),
                    "interview_duration_minutes": 0,
                    "agent_name": "Sarah",
                    "agent_version": "my-voic-agent-poc-conversational:2",
                    "status": "Pending",
                    "total_questions_asked": 0,
                    "submission_status": "Not Yet Submitted to cosmos"
                },
                "applicant": {
                    "phone": phone_number,
                    "application_type": "Life Insurance",
                    "policy_type": "Whole Life"
                },
            }
            container.create_item(body=item)
            return True

        # Run sync Cosmos DB operation in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_save)
        logger.info("Session data saved to Cosmos DB")

    async def _send_json(self, obj):
        """Sends a JSON object over WebSocket."""
        if self.ws:
            logger.debug("[VoiceLiveACSHandler] Sending JSON: %s", obj)
            await self.ws.send(json.dumps(obj))

    async def _flush_transcript_delta(self):
        """Flushes any pending transcript delta to the frontend."""
        if self._pending_transcript_delta:
            await self.send_message(
                json.dumps({"Kind": "TranscriptDelta", "Text": self._pending_transcript_delta})
            )
            self._pending_transcript_delta = ""

    async def _schedule_transcript_flush(self):
        """Schedules a delayed flush of transcript deltas.

        This batches multiple small text deltas into larger updates,
        reducing WebSocket traffic and preventing audio jitter caused
        by too-frequent React state updates on the frontend.
        """
        if self._transcript_delta_task is None or self._transcript_delta_task.done():
            async def delayed_flush():
                await asyncio.sleep(self._transcript_throttle_interval)
                await self._flush_transcript_delta()
            self._transcript_delta_task = asyncio.create_task(delayed_flush())

    async def _flush_audio_buffer(self):
        """Flushes accumulated audio to the frontend."""
        if self._audio_buffer:
            await self.send_message(bytes(self._audio_buffer))
            self._audio_buffer = bytearray()

    async def _queue_audio(self, audio_bytes: bytes):
        """Queues audio data for batched sending.

        Instead of sending every small audio chunk immediately,
        we accumulate them and send larger batches to reduce
        WebSocket message frequency and improve playback smoothness.
        """
        self._audio_buffer.extend(audio_bytes)

        # Send immediately if buffer is large enough
        if len(self._audio_buffer) >= self._audio_batch_size:
            await self._flush_audio_buffer()
        else:
            # Schedule a delayed flush if not already scheduled
            if self._audio_flush_task is None or self._audio_flush_task.done():
                async def delayed_audio_flush():
                    await asyncio.sleep(self._audio_flush_interval)
                    await self._flush_audio_buffer()
                self._audio_flush_task = asyncio.create_task(delayed_audio_flush())

    async def _sender_loop(self):
        """Continuously sends messages from the queue to the Voice Live WebSocket."""
        try:
            while True:
                msg = await self.send_queue.get()
                if self.ws:
                    await self.ws.send(msg)
                else:
                    # Voice Live WebSocket is disconnected - log and notify frontend
                    logger.warning("[VoiceLiveACSHandler] Voice Live WebSocket disconnected - audio not sent")
                    # Try to notify frontend about connection issue
                    try:
                        await self.send_message(json.dumps({
                            "Kind": "ConnectionLost",
                            "Message": "Voice connection lost. Please refresh the page."
                        }))
                    except:
                        pass
                    # Attempt to reconnect
                    await self._handle_error_and_restart(Exception("Voice Live WebSocket disconnected"))
                    break  # Exit loop after attempting restart
        except Exception:
            exc = sys.exc_info()[1]
            logger.exception("[VoiceLiveACSHandler] Sender loop error")
            await self._handle_error_and_restart(exc)


    async def _receiver_loop(self):
        """Handles incoming events from the Voice Live WebSocket."""
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")
                # Use DEBUG for frequent events to reduce I/O overhead
                logger.debug("[VoiceLiveACSHandler] Received event: %s", event_type)

                match event_type:
                    case "session.created":
                        session_id = event.get("session", {}).get("id")
                        self._current_session_id = session_id
                        logger.info("[VoiceLiveACSHandler] Session ID: %s", session_id)
                        self._response_in_progress = False
                        # Mark session as ready - AI introduction will be triggered
                        # by the frontend when user clicks "Start Application"
                        self._session_ready = True
                        phone_number = "8696728728"  # AcsEventHandler.phone_number
                        self._phone_number = phone_number
                        # Save to DB in background - don't block audio processing
                        asyncio.create_task(self.saveToDatabase(phone_number, session_id))
                        user_data = {"phone_number": phone_number, "session_id": session_id}

                        # Notify frontend of session creation with session_id
                        await self.send_message(json.dumps({
                            "Kind": "SessionCreated",
                            "SessionId": session_id,
                            "PhoneNumber": phone_number
                        }))

                        await self._send_json({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "system",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": f"User Context: {json.dumps(user_data)}"
                                    }
                                ]
                            }
                        })
                        logger.info("[VoiceLiveACSHandler] Session ready - auto-triggering AI introduction")
                        # Delay to let frontend audio system fully initialize and buffer to be ready
                        # Increased to 1.2s to ensure the audio worklet jitter buffer
                        # has time to accumulate enough samples before playback starts
                        # The intro is a long text, so we need extra buffer time
                        await asyncio.sleep(1.2)
                        # Auto-trigger AI introduction when session is ready
                        await self._trigger_ai_intro_internal()

                    case "response.created":
                        self._response_in_progress = True
                        logger.debug("[VoiceLiveACSHandler] Response started")

                    case "input_audio_buffer.cleared":
                        logger.info("Input Audio Buffer Cleared Message")

                    case "input_audio_buffer.speech_started":
                        logger.info(
                            "Voice activity detection started at %s ms",
                            event.get("audio_start_ms"),
                        )
                        await self.stop_audio()

                    case "input_audio_buffer.speech_stopped":
                        logger.info("Speech stopped")
                        transcript = self._last_transcript
                        logger.info("[ReceiverLoop] Final transcript before stopping: %s", transcript)
                        # await self._send_json({
                        #     "type": "response.create",
                        #     "response": {
                        #         "instructions": f"User said: {transcript}"
                        #     }
                        # })

                    # case "conversation.item.input_audio_transcription.completed":
                    #     transcript = event.get("transcript")
                    #     logger.info("User: %s", transcript)
                    #     # Query the knowledge base
                    #     response_text = await self.query_knowledge_base(transcript)
                    #     await self.send_message(
                    #         json.dumps({"Kind": "Transcription", "Text": response_text})
                    #     )
                    case "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript")
                        self._last_transcript = transcript
                        logger.info("[ReceiverLoop] User transcript: %s", transcript)
                        # Filter phantom transcripts: ignore very short transcripts (likely noise/echo)
                        # and common Whisper hallucinations
                        MIN_TRANSCRIPT_LENGTH = 3
                        HALLUCINATION_PATTERNS = {"with", "with...", "um", "uh", "okay", "ok", "hmm"}

                        transcript_clean = transcript.strip() if transcript else ""
                        is_valid_transcript = (
                            len(transcript_clean) >= MIN_TRANSCRIPT_LENGTH and
                            transcript_clean.lower() not in HALLUCINATION_PATTERNS
                        )

                        # Track user message in conversation history (only valid transcripts)
                        if transcript and is_valid_transcript:
                            self._conversation_history.append({
                                'role': 'user',
                                'content': transcript,
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            })
                            # Send user transcript to client
                            await self.send_message(
                                json.dumps({"Kind": "UserTranscription", "Text": transcript})
                            )
                        elif transcript:
                            logger.info("[ReceiverLoop] Filtered phantom transcript: '%s'", transcript)
                        
                        # # Query the knowledge base
                        # response_text = await self.query_knowledge_base(transcript)
                        # logger.info("[ReceiverLoop] Response from knowledge base: %s", response_text)
                        # # 
                        
                        # await self._send_json({
                        #             "type": "response.create",
                        #             "response": {
                        #                 "instructions": f"Answer using this knowledge base info:\n{response_text}"
                        #             }
                        #         })
                        # await self.send_message(json.dumps({"Kind": "Transcription", "Text": response_text}))


                    case "conversation.item.input_audio_transcription.failed":
                        error_msg = event.get("error")
                        logger.warning("Transcription Error: %s", error_msg)

                    case "output_audio_buffer.stopped":
                        logger.info("Output audio buffer stopped")
                        #  delete the agent

                    case "response.done":
                        self._response_in_progress = False
                        response = event.get("response", {})
                        logger.info("Response Done: Id=%s", response.get("id"))
                        if response.get("status_details"):
                            logger.info(
                                "Status Details: %s",
                                json.dumps(response["status_details"], indent=2),
                            )
                        # Flush any remaining audio before signaling completion
                        await self._flush_audio_buffer()
                        # Notify client that response is complete (for voice status)
                        await self.send_message(
                            json.dumps({"Kind": "ResponseDone"})
                        )

                    case "response.audio_transcript.delta":
                        delta_text = event.get("delta", "")
                        if delta_text:
                            # Accumulate and send throttled updates to frontend
                            # With improved audio buffering, text streaming should be smooth
                            self._pending_transcript_delta += delta_text
                            await self._schedule_transcript_flush()

                    case "response.audio_transcript.done":
                        # Flush any pending transcript delta before sending done
                        await self._flush_transcript_delta()
                        transcript = event.get("transcript")
                        logger.info("AI: %s", transcript)
                        # Track AI message in conversation history
                        if transcript:
                            self._conversation_history.append({
                                'role': 'ai',
                                'content': transcript,
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            })
                        await self.send_message(
                            json.dumps({"Kind": "TranscriptDone", "Text": transcript})
                        )

                    # Text-only responses (for chat mode with modalities: ["text"])
                    case "response.text.delta":
                        delta_text = event.get("delta", "")
                        if delta_text:
                            # Accumulate and send throttled updates to frontend
                            self._pending_transcript_delta += delta_text
                            await self._schedule_transcript_flush()

                    case "response.text.done":
                        # Flush any pending transcript delta before sending done
                        await self._flush_transcript_delta()
                        text = event.get("text", "")
                        logger.info("AI (text): %s", text[:100] if text else "")
                        # Track AI message in conversation history (text mode)
                        if text:
                            self._conversation_history.append({
                                'role': 'ai',
                                'content': text,
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            })
                        await self.send_message(
                            json.dumps({"Kind": "TranscriptDone", "Text": text})
                        )

                    case "response.audio.delta":
                        delta = event.get("delta")
                        if self.is_raw_audio:
                            audio_bytes = base64.b64decode(delta)
                            # Use audio batching to reduce WebSocket message frequency
                            await self._queue_audio(audio_bytes)
                        else:
                            await self.voicelive_to_acs(delta)

                    case "error":
                        logger.error("Voice Live Error: %s", event)

                    case _:
                        logger.debug(
                            "[VoiceLiveACSHandler] Other event: %s", event_type
                        )
        except Exception:
            exc = sys.exc_info()[1]
            logger.exception("[VoiceLiveACSHandler] Receiver loop error")
            await self._handle_error_and_restart(exc)

    async def send_message(self, message: Data):
        """Sends data back to client WebSocket."""
        try:
            await self.incoming_websocket.send(message)
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Failed to send message")

    async def voicelive_to_acs(self, base64_data):
        """Converts Voice Live audio delta to ACS audio message."""
        try:
            data = {
                "Kind": "AudioData",
                "AudioData": {"Data": base64_data},
                "StopAudio": None,
            }
            await self.send_message(json.dumps(data))
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error in voicelive_to_acs")

    async def stop_audio(self):
        """Sends a StopAudio signal to ACS."""
        stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
        await self.send_message(json.dumps(stop_audio_data))

    async def acs_to_voicelive(self, stream_data):
        """Processes audio from ACS and forwards to Voice Live if not silent."""
        try:
            data = json.loads(stream_data)
            logger.info("[VoiceLiveACSHandler] Received ACS data: %s", data)
            if data.get("kind") == "AudioData":
                audio_data = data.get("audioData", {})
                if not audio_data.get("silent", True):
                    await self.audio_to_voicelive(audio_data.get("data"))
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error processing ACS audio")

    async def web_to_voicelive(self, message):
        """Handles incoming WebSocket messages from web client.

        Supports both:
        - Binary audio data (for voice mode)
        - JSON text messages (for chat mode)
        """
        # Check if it's binary audio data or JSON text message
        if isinstance(message, bytes):
            # Binary audio - encode and send to Voice Live
            audio_b64 = base64.b64encode(message).decode("ascii")
            await self.audio_to_voicelive(audio_b64)
        elif isinstance(message, str):
            # JSON message - parse and handle
            try:
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "text_input":
                    # Text chat message - send via conversation.item.create
                    text = data.get("text", "")
                    if text:
                        await self.send_text_message(text)
                elif msg_type == "session_start":
                    # Session start trigger - AI introduces itself via voice
                    logger.info("[VoiceLiveACSHandler] Session start - triggering AI introduction")
                    await self.trigger_ai_introduction()
                elif msg_type == "ping":
                    # Keep-alive ping
                    await self.send_message(json.dumps({"Kind": "Pong"}))
                elif msg_type == "transfer_to_agent":
                    # User requested transfer to human agent
                    reason = data.get("reason", "User requested human agent")
                    custom_agent_phone = data.get("agent_phone")  # Optional custom phone for demo
                    logger.info("[VoiceLiveACSHandler] Transfer to agent requested: %s", reason)
                    if custom_agent_phone:
                        logger.info("[VoiceLiveACSHandler] Using custom agent phone: %s", custom_agent_phone)
                    await self.initiate_agent_transfer(reason, custom_agent_phone)
                else:
                    logger.warning("[VoiceLiveACSHandler] Unknown message type: %s", msg_type)
            except json.JSONDecodeError:
                logger.warning("[VoiceLiveACSHandler] Invalid JSON message: %s", message[:100])

    async def _trigger_ai_intro_internal(self):
        """Internal method to trigger AI introduction - called when session is already ready."""
        logger.info("[VoiceLiveACSHandler] Triggering AI introduction (internal)")

        # Create a conversation item to prompt the AI to introduce itself
        await self._send_json({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Hello, I'm ready to start my insurance application."
                    }
                ]
            }
        })

        # Trigger response generation with audio
        await self._send_json({
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"]
            }
        })

    async def trigger_ai_introduction(self):
        """Triggers the AI to introduce itself at the start of a session.

        For Agent service, we can't override instructions in response.create.
        Instead, we send a conversation item to prompt the AI to introduce itself.
        """
        # Wait for session to be ready (with timeout)
        if not self._session_ready:
            logger.info("[VoiceLiveACSHandler] Waiting for Voice Live session to be ready...")
            for _ in range(50):  # Wait up to 5 seconds
                if self._session_ready:
                    break
                await asyncio.sleep(0.1)

            if not self._session_ready:
                logger.warning("[VoiceLiveACSHandler] Session not ready after timeout, aborting introduction")
                return

        await self._trigger_ai_intro_internal()

    async def send_text_message(self, text: str):
        """Sends a text message to Voice Live API using conversation.item.create.

        This allows text input in the same session as voice, maintaining context.
        """
        logger.info("[VoiceLiveACSHandler] Sending text message: %s", text[:50])

        # Cancel any active response before sending new input
        if self._response_in_progress:
            logger.info("[VoiceLiveACSHandler] Canceling active response before new text input")
            await self._send_json({"type": "response.cancel"})
            # Brief delay to allow cancel to process
            await asyncio.sleep(0.1)
            self._response_in_progress = False

        # Create conversation item with text input
        await self._send_json({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": text
                    }
                ]
            }
        })

        # Trigger response generation (text only, no audio needed for chat)
        await self._send_json({
            "type": "response.create",
            "response": {
                "modalities": ["text"]  # Text only for chat mode
            }
        })

    async def initiate_agent_transfer(self, reason: str, custom_agent_phone: str = None):
        """Initiate transfer to a human agent.

        This method:
        1. Generates a conversation summary
        2. Saves the conversation to Cosmos DB
        3. Notifies the frontend of transfer status
        4. (For phone calls) Triggers ACS call transfer

        Args:
            reason: The reason for the transfer request
            custom_agent_phone: Optional custom phone number (for demo purposes)
        """
        logger.info("[VoiceLiveACSHandler] Initiating agent transfer: %s", reason)

        # Notify frontend that transfer is starting
        await self.send_message(json.dumps({
            "Kind": "TransferInitiated",
            "SessionId": self._current_session_id
        }))

        try:
            # Use transfer handler to process the transfer
            result = await self._transfer_handler.initiate_transfer(
                session_id=self._current_session_id,
                reason=reason,
                messages=self._conversation_history,
                phone_number=self._phone_number
            )

            if result.get('success'):
                logger.info("[VoiceLiveACSHandler] Transfer request saved, status: pending")

                # Use custom phone if provided, otherwise use configured default
                agent_phone = custom_agent_phone or self._transfer_handler.agent_phone

                # Notify frontend of progress
                await self.send_message(json.dumps({
                    "Kind": "TransferInProgress",
                    "AgentPhone": agent_phone,
                    "Summary": result.get('transfer', {}).get('summary', '')
                }))

                # Make outbound call to agent's phone
                if agent_phone and self._acs_client:
                    logger.info("[VoiceLiveACSHandler] Making outbound call to agent: %s", agent_phone)
                    try:
                        # Get the summary to speak to the agent
                        summary = result.get('transfer', {}).get('summary', '')
                        await self._call_agent_phone(agent_phone, summary)
                        await self._transfer_handler.update_transfer_status(
                            self._current_session_id,
                            'in_progress'
                        )
                        # Final notification to frontend
                        await self.send_message(json.dumps({
                            "Kind": "TransferComplete",
                            "Message": f"Calling agent at {agent_phone}. Please wait while we connect you."
                        }))
                    except Exception as call_err:
                        logger.error("[VoiceLiveACSHandler] Failed to call agent: %s", call_err)
                        await self._transfer_handler.update_transfer_status(
                            self._current_session_id,
                            'pending'
                        )
                        await self.send_message(json.dumps({
                            "Kind": "TransferComplete",
                            "Message": "Your request has been submitted. An agent will review your conversation and contact you shortly."
                        }))
                else:
                    # No ACS client or agent phone configured - agent will call back manually
                    await self._transfer_handler.update_transfer_status(
                        self._current_session_id,
                        'pending'
                    )
                    await self.send_message(json.dumps({
                        "Kind": "TransferComplete",
                        "Message": "Your request has been submitted. An agent will review your conversation and contact you shortly."
                    }))

            else:
                raise Exception(result.get('error', 'Unknown error'))

        except Exception as e:
            logger.error("[VoiceLiveACSHandler] Transfer failed: %s", e)
            await self.send_message(json.dumps({
                "Kind": "TransferFailed",
                "Error": str(e)
            }))

    async def _call_agent_phone(self, agent_phone: str, summary: str = ""):
        """Make an outbound call to the agent's phone with a spoken message.

        This is used when a web client requests transfer - since there's no
        existing phone call to transfer, we initiate an outbound call to the agent
        and play a text-to-speech message with the transfer details.

        Args:
            agent_phone: The agent's phone number in E.164 format (e.g., +1234567890)
            summary: Conversation summary to speak to the agent

        Requires:
            - ACS_SOURCE_PHONE_NUMBER in config (your purchased ACS phone number)
        """
        if not self._acs_client:
            raise Exception("ACS client not configured")

        if not self._acs_source_phone:
            logger.warning("[VoiceLiveACSHandler] No source phone number configured (ACS_SOURCE_PHONE_NUMBER). Cannot make outbound call.")
            raise Exception("ACS source phone number not configured. Add ACS_SOURCE_PHONE_NUMBER to your .env file.")

        logger.info("[VoiceLiveACSHandler] Initiating outbound call to agent: %s from %s",
                    agent_phone, self._acs_source_phone)

        # Create the target phone number identifier
        target = PhoneNumberIdentifier(agent_phone)
        source = PhoneNumberIdentifier(self._acs_source_phone)

        # Build callback URL for call events - this will handle playing the message
        # callback_url = f"{self._acs_callback_url}/acs/callbacks/agent-transfer-{self._current_session_id}"
        callback_url = f"{self._acs_callback_url}/acs/agent-transfer-callback/{self._current_session_id}"

        # Prepare the message to speak to the agent
        customer_phone = self._phone_number or "unknown"
        message_text = f"Hello, you have a new customer transfer request. "
        if summary:
            # Clean up summary for speech
            clean_summary = summary.replace('\n', '. ').replace('  ', ' ')[:500]
            message_text += f"Here is the conversation summary: {clean_summary}. "
        message_text += f"The customer's phone number is {customer_phone}. Please check the agent dashboard for full details. Thank you."

        try:
            # Import the necessary types for cognitive services
            from azure.communication.callautomation import (
                TextSource,
                CallInvite,
                CommunicationUserIdentifier
            )

            # Build cognitive services endpoint URL
            # cog_endpoint = self._cognitive_services_endpoint.rstrip('/') if self._cognitive_services_endpoint else None
            # logger.info("[VoiceLiveACSHandler] Using Cognitive Services endpoint: %s", cog_endpoint)
            config = {
                    "ACS_CONNECTION_STRING": os.getenv("ACS_CONNECTION_STRING"),
                    "ACS_DEV_TUNNEL": os.getenv("ACS_DEV_TUNNEL"),
                    }
                            
            acs = AcsEventHandler(config)
                            # host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
            logger.info("Host URL for outbound call: %s", "http://localhost:8000/")
            call_result = await acs.make_outbound_call(session_id=self._current_session_id, host_url="http://localhost:8000/", config=config)
            call_connection_id = call_result.call_connection_id
            self.active_calls[self._current_session_id] = call_connection_id
            
            logger.info(
                "[AgentCallManager] Call initiated: session=%s, call_id=%s",
                self._current_session_id,
                call_connection_id
            )

            # Create the outbound call with cognitive services for TTS
            # call_result = await self._acs_client.create_call(
            #     target_participant=target,
            #     callback_url=callback_url,
            #     source_caller_id_number=source,
            #     operation_context=f"agent-transfer-{self._current_session_id}",
            #     cognitive_services_endpoint=cog_endpoint
            # )


            # Start a background task to monitor the call and play message when answered
            # asyncio.create_task(self._monitor_agent_call(
            #     call_result.call_connection_id,
            #     message_text
            # ))

            return call_result

        except Exception as e:
            logger.error("[VoiceLiveACSHandler] Failed to create outbound call: %s", e)
            raise

    async def _monitor_agent_call(self, call_connection_id: str, message: str):
        """Monitor the agent call and play TTS message when answered.

        Args:
            call_connection_id: The call connection ID
            message: The message to speak to the agent
        """
        logger.info("[VoiceLiveACSHandler] Monitoring agent call: %s", call_connection_id)

        max_wait = 60  # Wait up to 60 seconds for agent to answer
        poll_interval = 1

        for i in range(max_wait):
            await asyncio.sleep(poll_interval)

            try:
                call_conn = self._acs_client.get_call_connection(call_connection_id)
                props = await call_conn.get_call_properties()
                state = props.call_connection_state

                logger.debug("[VoiceLiveACSHandler] Agent call state: %s", state)

                if state == "connected":
                    logger.info("[VoiceLiveACSHandler] Agent answered! Playing message...")
                    await self._play_message_to_agent(call_connection_id, message)
                    return

                elif state in ["disconnected", "terminated"]:
                    logger.info("[VoiceLiveACSHandler] Agent call ended without answer")
                    return

            except Exception as e:
                if "8522" in str(e) or "not found" in str(e).lower():
                    logger.info("[VoiceLiveACSHandler] Agent call ended")
                    return
                logger.warning("[VoiceLiveACSHandler] Error checking call state: %s", e)

        logger.warning("[VoiceLiveACSHandler] Agent call monitoring timed out")

    async def _play_message_to_agent(self, call_connection_id: str, message: str):
        """Play a TTS message to the agent.

        Args:
            call_connection_id: The call connection ID
            message: The message to speak
        """
        try:
            from azure.communication.callautomation import TextSource

            call_conn = self._acs_client.get_call_connection(call_connection_id)

            # Create text source for TTS
            text_source = TextSource(
                text=message,
                voice_name="en-US-JennyNeural"  # Azure neural voice
            )

            # Play the message
            await call_conn.play_media(
                play_source=text_source,
                play_to=[]  # Empty list means play to all participants
            )

            logger.info("[VoiceLiveACSHandler] Message played to agent")

        except Exception as e:
            logger.error("[VoiceLiveACSHandler] Failed to play message to agent: %s", e)
            logger.info("[VoiceLiveACSHandler] TTS not available - agent should check dashboard for details")
            # Keep the call connected for a few seconds so agent knows something happened
            await asyncio.sleep(5)
            # Then hang up
            try:
                call_conn = self._acs_client.get_call_connection(call_connection_id)
                await call_conn.hang_up(is_for_everyone=True)
            except:
                pass

    async def _handle_error_and_restart(self, exc, max_retries: int = 3):
        """Attempt simple recovery on errors: resend session config and 'response.create', then reconnect."""
        logger.error("[VoiceLiveACSHandler] Handling exception and attempting restart: %s", exc)

        # Helper to check if websocket is closed
        def is_ws_closed():
            if self.ws is None:
                return True
            try:
                # Try different ways to check if closed (websockets library version compatibility)
                if hasattr(self.ws, 'closed'):
                    return self.ws.closed
                if hasattr(self.ws, 'state'):
                    return self.ws.state.name == 'CLOSED'
                return False
            except:
                return True

        # quick attempts to re-trigger session and response
        for attempt in range(1, max_retries + 1):
            try:
                logger.info("[VoiceLiveACSHandler] Restart attempt %d/%d", attempt, max_retries)
                if is_ws_closed():
                    # reconnect websocket and re-send session config
                    endpoint = self.endpoint.rstrip("/")
                    agent_name = self.agent_name.strip()
                    project_name = self.foundry_project_name.strip()
                    url = f"{endpoint}/voice-live/realtime?api-version=2025-10-01&x-ms-client-request-id={self._generate_guid()}&agent_name={agent_name}&agent-project-name={project_name}"
                    url = url.replace("https://", "wss://")

                    # Use API key if available
                    if self.api_key:
                        headers = {"api-key": self.api_key}
                    elif self.client_id:
                        async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                            token = await credential.get_token("https://cognitiveservices.azure.com/.default")
                            headers = {"Authorization": f"Bearer {token.token}"}
                    else:
                        agent_access_token = (await DefaultAzureCredential().get_token("https://ai.azure.com/.default")).token
                        headers = {"Authorization": f"Bearer {agent_access_token}"}

                    logger.info("[VoiceLiveACSHandler] Reconnecting to: %s", url)
                    self.ws = await websockets.connect(url, additional_headers=headers, ping_interval=30, ping_timeout=60)
                
                await self._send_json(session_config())
                await self._send_json({"type": "response.create", "response": {"instructions": "Restarting response after an internal error."}})
                logger.info("[VoiceLiveACSHandler] Restart successful on attempt %d", attempt)
                return
            except Exception as e:
                logger.exception("[VoiceLiveACSHandler] Restart attempt %d failed: %s", attempt, e)
                await asyncio.sleep(2 ** attempt)

        # if restarts failed, try full reconnect with backoff
        try:
            logger.warning("[VoiceLiveACSHandler] Restart attempts exhausted, performing full reconnect")
            if self.ws:
                await self.ws.close()
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error while closing websocket")
        await asyncio.sleep(2)
        try:
            await self.connect()
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Reconnect failed; will need manual intervention")

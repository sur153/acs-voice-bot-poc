"""Handles media streaming to Azure Voice Live API via WebSocket."""

import asyncio
import base64
import json
import logging
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
logger = logging.getLogger(__name__)

def session_config():
    """Returns the default session configuration for Voice Live.

    LATENCY OPTIMIZED: Balanced for speed without cutting off words.
    Original values preserved in comments for rollback if needed.
    """
    return {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.5,           # Was 0.3 - higher to avoid false triggers on short words
                "prefix_padding_ms": 200,   # Was 100 - increased to capture word beginnings
                "silence_duration_ms": 300, # Was 100 - increased to avoid cutting off words
                "remove_filler_words": False, # Disabled - was removing parts of valid words
                "end_of_utterance_detection": {
                    "model": "semantic_detection_v1",
                    "threshold": 0.3,       # Was 0.01 - balanced threshold
                    "timeout": 1.2,         # Was 2 - reduced but not too aggressive
                },
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "en"
            },
            "voice": {
                "name": "en-IN-AartiNeural",
                "type": "azure-standard",
                "temperature": 0,
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
        if self.api_key:
            headers = {"api-key": self.api_key}
            logger.info("[VoiceLiveACSHandler] Using API key authentication")
        elif self.client_id:
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

        # try:
        #     # self.ws = await websockets.connect(url, additional_headers=headers)
        #     logger.info("Connected to Azure Voice API before with agent: %s", agent_id or "default")
        #     self.ws = await ws_connect(
        #         url,
        #         additional_headers=headers,
        #         ping_interval=30,  # Send a ping every 30 seconds
        #         ping_timeout=60,   # Wait up to 60 seconds for a pong response
        #     )
           
        #     logger.info("Connected to Azure Voice API with agent: %s", agent_id or "default")

        # except Exception as e:
        #     logger.info("[VoiceLiveACSHandler] Error connecting to Voice Live API: %s", e)
        #     return
        logger.info("[VoiceLiveACSHandler] Connecting to URL: %s", url)
        logger.info("[VoiceLiveACSHandler] Using headers: %s", {k: v[:20] + '...' if len(str(v)) > 20 else v for k, v in headers.items()})
        try:
            self.ws = await websockets.connect(url, additional_headers=headers, ping_interval=30, ping_timeout=60)
            logger.info("[VoiceLiveACSHandler] WebSocket connected successfully")
        except Exception as e:
            logger.error("[VoiceLiveACSHandler] Failed to connect: %s", e)
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
    
    async def _send_json(self, obj):
        """Sends a JSON object over WebSocket."""
        if self.ws:
            logger.debug("[VoiceLiveACSHandler] Sending JSON: %s", obj)
            await self.ws.send(json.dumps(obj))

    async def _sender_loop(self):
        """Continuously sends messages from the queue to the Voice Live WebSocket."""
        try:
            while True:
                msg = await self.send_queue.get()
                if self.ws:
                    await self.ws.send(msg)
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
                        logger.info("[VoiceLiveACSHandler] Session ID: %s", session_id)
                        self._response_in_progress = False

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
                        # Send user transcript to client
                        if transcript:
                            await self.send_message(
                                json.dumps({"Kind": "UserTranscription", "Text": transcript})
                            )
                        
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
                        # Notify client that response is complete (for voice status)
                        await self.send_message(
                            json.dumps({"Kind": "ResponseDone"})
                        )

                    case "response.audio_transcript.delta":
                        delta_text = event.get("delta", "")
                        if delta_text:
                            await self.send_message(
                                json.dumps({"Kind": "TranscriptDelta", "Text": delta_text})
                            )

                    case "response.audio_transcript.done":
                        transcript = event.get("transcript")
                        logger.info("AI: %s", transcript)
                        await self.send_message(
                            json.dumps({"Kind": "TranscriptDone", "Text": transcript})
                        )

                    # Text-only responses (for chat mode with modalities: ["text"])
                    case "response.text.delta":
                        delta_text = event.get("delta", "")
                        if delta_text:
                            await self.send_message(
                                json.dumps({"Kind": "TranscriptDelta", "Text": delta_text})
                            )

                    case "response.text.done":
                        text = event.get("text", "")
                        logger.info("AI (text): %s", text[:100] if text else "")
                        await self.send_message(
                            json.dumps({"Kind": "TranscriptDone", "Text": text})
                        )

                    case "response.audio.delta":
                        delta = event.get("delta")
                        if self.is_raw_audio:
                            audio_bytes = base64.b64decode(delta)
                            await self.send_message(audio_bytes)
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
                elif msg_type == "ping":
                    # Keep-alive ping
                    await self.send_message(json.dumps({"Kind": "Pong"}))
                else:
                    logger.warning("[VoiceLiveACSHandler] Unknown message type: %s", msg_type)
            except json.JSONDecodeError:
                logger.warning("[VoiceLiveACSHandler] Invalid JSON message: %s", message[:100])

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

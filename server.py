import asyncio
import base64
import logging
import os

import json

from app.handler.acs_event_handler import AcsEventHandler
from app.handler.acs_media_handler import ACSMediaHandler
from app.handler.chat_handler import ChatHandler
from app.handler.transfer_handler import TransferHandler
from app.handler import acs_cosmos_client
from dotenv import load_dotenv
from quart import Quart, request, websocket, jsonify

load_dotenv()

app = Quart(__name__)
app.config["AZURE_VOICE_LIVE_API_KEY"] = os.getenv("AZURE_VOICE_LIVE_API_KEY", "")
app.config["AZURE_VOICE_LIVE_ENDPOINT"] = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
app.config["VOICE_LIVE_MODEL"] = os.getenv("VOICE_LIVE_MODEL", "gpt-4o-mini")
app.config["ACS_CONNECTION_STRING"] = os.getenv("ACS_CONNECTION_STRING")
app.config["ACS_DEV_TUNNEL"] = os.getenv("ACS_DEV_TUNNEL", "")
app.config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"] = os.getenv(
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", ""
)
app.config["AZURE_SEARCH_ENDPOINT"] = os.getenv("AZURE_SEARCH_ENDPOINT")
app.config["AZURE_SEARCH_INDEX_NAME"] = os.getenv("AZURE_SEARCH_INDEX_NAME")
app.config["AZURE_SEARCH_API_KEY"] = os.getenv("AZURE_SEARCH_API_KEY")
app.config["MODEL_DEPLOYMENT_NAME"] = os.getenv("MODEL_DEPLOYMENT_NAME")
app.config["PROJECT_ENDPOINT"] = os.getenv("PROJECT_ENDPOINT")
app.config["AZURE_VOICELIVE_PROJECT_NAME"] = os.getenv("AZURE_VOICELIVE_PROJECT_NAME")
app.config["AZURE_VOICELIVE_AGENT_ID"] = os.getenv("AZURE_VOICELIVE_AGENT_ID")
app.config["AZURE_VOICELIVE_AGENT_NAME"] = os.getenv("AZURE_VOICELIVE_AGENT_NAME")
app.config["AGENT_PHONE_NUMBER"] = os.getenv("AGENT_PHONE_NUMBER", "")
app.config["ACS_SOURCE_PHONE_NUMBER"] = os.getenv("ACS_SOURCE_PHONE_NUMBER", "")  # Your purchased ACS phone number for outbound calls

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s"
)

acs_handler = AcsEventHandler(app.config)
chat_handler = ChatHandler(app.config)
transfer_handler = TransferHandler(app.config)


@app.route("/acs/incomingcall", methods=["POST"])
async def incoming_call_handler():
    """Handles initial incoming call event from EventGrid."""
    events = await request.get_json()
    host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
    return await acs_handler.process_incoming_call(events, host_url, app.config)


@app.route("/acs/callbacks/<context_id>", methods=["POST"])
async def acs_event_callbacks(context_id):
    """Handles ACS event callbacks for call connection and streaming events."""
    raw_events = await request.get_json()
    return await acs_handler.process_callback_events(context_id, raw_events, app.config)


@app.websocket("/acs/ws")
async def acs_ws():
    """WebSocket endpoint for ACS to send audio to Voice Live."""
    logger = logging.getLogger("acs_ws")
    logger.info("Incoming ACS WebSocket connection")
    handler = ACSMediaHandler(app.config)
    await handler.init_incoming_websocket(websocket, is_raw_audio=False)
    asyncio.create_task(handler.connect())
    try:
        while True:
            msg = await websocket.receive()
            logger.info("Received message from ACS WebSocket: %s", msg)
            await handler.acs_to_voicelive(msg)
    except Exception:
        logger.exception("ACS WebSocket connection closed")


@app.websocket("/web/ws")
async def web_ws():
    """WebSocket endpoint for web clients to send audio to Voice Live."""
    logger = logging.getLogger("web_ws")
    logger.info("Incoming Web WebSocket connection")
    handler = ACSMediaHandler(app.config)
    await handler.init_incoming_websocket(websocket, is_raw_audio=True)
    asyncio.create_task(handler.connect())
    try:
        while True:
            msg = await websocket.receive()
            await handler.web_to_voicelive(msg)
    except Exception:
        logger.exception("Web WebSocket connection closed")

@app.websocket("/acs/agent-ws")
async def agent_call_ws():
    """WebSocket for agent call audio streaming."""
    logger = logging.getLogger("agent_call_ws")
    logger.info("Agent call WebSocket connected")
    
    try:
        while True:
            msg = await websocket.receive()
            data = json.loads(msg)
            if data.get("kind") == "AudioData":
                # b64_audio = data["audioData"]["data"]
                # pcm_audio = base64.b64decode(b64_audio)

                # logger.info(
                #     "🔊 Decoded PCM audio: %d bytes | silent=%s",
                #     len(pcm_audio),
                #     data["audioData"].get("silent"),
                # )
                await websocket.send(msg)
            
            logger.debug("Agent audio received")
    except Exception as e:
        logger.exception("Agent WebSocket error")

@app.route("/")
async def index():
    """Serves the static index page."""
    return await app.send_static_file("index.html")


@app.route("/api/chat", methods=["POST"])
async def chat_api():
    """REST endpoint for text chat - completely separate from voice WebSocket.

    Request body:
        {
            "message": "user's message",
            "history": [{"role": "user/assistant", "content": "..."}]  # optional
        }

    Response:
        {
            "response": "assistant's reply",
            "status": "success/error"
        }
    """
    logger = logging.getLogger("chat_api")
    try:
        data = await request.get_json()
        message = data.get("message", "")
        history = data.get("history", [])

        if not message.strip():
            return jsonify({"response": "", "status": "error", "error": "Empty message"}), 400

        logger.info(f"Chat request: {message[:50]}...")
        result = await chat_handler.get_response(message, history)
        return jsonify(result)

    except Exception as e:
        logger.exception(f"Error in chat endpoint: {e}")
        return jsonify({
            "response": "Sorry, something went wrong. Please try again.",
            "status": "error",
            "error": str(e)
        }), 500


@app.route("/agent")
async def agent_dashboard():
    """Serves the agent dashboard page."""
    return await app.send_static_file("agent.html")


@app.route("/api/session/<session_id>/collected-data", methods=["GET"])
async def get_collected_data(session_id):
    """Get MCP-collected conversation data for a session.

    This endpoint retrieves the official structured data collected by the AI agent
    via MCP and stored in the query_response container. This is the authoritative
    source for applicant responses, not the frontend's transcript extraction.

    Args:
        session_id: The Voice Live session ID (e.g., 'sess_xxx')

    Returns:
        JSON with conversation_data including:
        - meta: session info, status
        - applicant: full_name, phone_number
        - consent: recording_consent, hipaa_acknowledged
        - responses: categorized Q&A (personal, employment, health_history, etc.)
        - navigation_path: ordered list of questions answered
    """
    logger = logging.getLogger("collected_data_api")
    try:
        # Run synchronous Cosmos query in thread pool to avoid blocking the event loop
        # This prevents audio jitter and WebSocket delays during polling
        data = await asyncio.to_thread(acs_cosmos_client.get_mcp_conversation_data, session_id)
        if data:
            return jsonify({
                "status": "success",
                "data": data
            })
        else:
            return jsonify({
                "status": "not_found",
                "data": None,
                "message": "No collected data found yet for this session"
            }), 200  # 200 not 404 - data may not exist yet during active session

    except Exception as e:
        logger.exception(f"Error fetching collected data: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@app.route("/agent/transfers", methods=["GET"])
async def get_pending_transfers():
    """Get all pending transfer requests for the agent dashboard.

    Returns:
        JSON list of pending transfers with session info and conversation context.
    """
    logger = logging.getLogger("agent_api")
    try:
        # Run in thread pool to avoid blocking
        transfers = await asyncio.to_thread(acs_cosmos_client.get_pending_transfers)
        return jsonify({
            "status": "success",
            "transfers": transfers,
            "count": len(transfers)
        })
    except Exception as e:
        logger.exception(f"Error fetching pending transfers: {e}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "transfers": []
        }), 500


@app.route("/agent/transfers/<session_id>", methods=["GET"])
async def get_transfer_details(session_id):
    """Get full details for a specific transfer.

    Args:
        session_id: The session ID (without 'session-' prefix)

    Returns:
        JSON with full session document including conversation history.
    """
    logger = logging.getLogger("agent_api")
    try:
        # Run in thread pool to avoid blocking
        details = await asyncio.to_thread(acs_cosmos_client.get_transfer_by_session, session_id)
        if details:
            return jsonify({
                "status": "success",
                "session": details
            })
        else:
            return jsonify({
                "status": "error",
                "error": "Session not found"
            }), 404
    except Exception as e:
        logger.exception(f"Error fetching transfer details: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@app.route("/agent/transfers/<session_id>/status", methods=["PUT"])
async def update_transfer_status(session_id):
    """Update the status of a transfer.

    Request body:
        {
            "status": "completed" | "failed" | "in_progress",
            "notes": "Optional agent notes"
        }

    Args:
        session_id: The session ID (without 'session-' prefix)

    Returns:
        JSON with success status.
    """
    logger = logging.getLogger("agent_api")
    try:
        data = await request.get_json()
        new_status = data.get("status")
        notes = data.get("notes", "")

        if new_status not in ["pending", "in_progress", "completed", "failed"]:
            return jsonify({
                "status": "error",
                "error": "Invalid status. Must be: pending, in_progress, completed, or failed"
            }), 400

        additional_info = {}
        if notes:
            additional_info["agent_notes"] = notes

        # Run in thread pool to avoid blocking
        success = await asyncio.to_thread(
            acs_cosmos_client.update_transfer_status,
            session_id,
            new_status,
            additional_info if additional_info else None
        )

        if success:
            return jsonify({
                "status": "success",
                "message": f"Transfer status updated to {new_status}"
            })
        else:
            return jsonify({
                "status": "error",
                "error": "Failed to update transfer status"
            }), 500

    except Exception as e:
        logger.exception(f"Error updating transfer status: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@app.route('/acs/agent-transfer-callback/<session_id>', methods=['POST'])
async def callback_events_handler(session_id):
    # ACS sends events as a list of
    # JSON objects
    raw_events = await request.get_json()
    return await acs_handler.process_callback_events_human_agent(raw_events, app.config)

    # for event_data in request.json:
    #     event_type = event_data.get("type")
    #     data = event_data.get("data", {})
    #     call_connection_id = data.get("callConnectionId")

    #     print(f"Received Event: {event_type} for Call ID: {call_connection_id}")

    #     # 1. Handle when the call is successfully connected
    #     if event_type == "Microsoft.Communication.CallConnected":
    #         # Play a greeting message using Text-to-Speech
    #         text_to_play = TextSource(text="Hello! Thank you for answering this call.")
            
    #         # Get the connection object for this specific call
    #         call_connection = client.get_call_connection(call_connection_id)
    #         call_connection.play_media_to_all(text_to_play)
    #         print("Greeting played.")

    #     # 2. Handle when the call ends
    #     elif event_type == "Microsoft.Communication.CallDisconnected":
    #         print(f"Call {call_connection_id} has ended.")

    #     # 3. Handle failures (optional)
    #     elif event_type == "Microsoft.Communication.PlayFailed":
    #         print("Failed to play the audio message.")

    # return Response(status=200)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)

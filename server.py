import asyncio
import logging
import os

from app.handler.acs_event_handler import AcsEventHandler
from app.handler.acs_media_handler import ACSMediaHandler
from app.handler.chat_handler import ChatHandler
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

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s"
)

acs_handler = AcsEventHandler(app.config)
chat_handler = ChatHandler(app.config)


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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)

"""Handler for processing ACS (Azure Communication Services) call and callback events."""

import json
import logging
import uuid
from urllib.parse import urlencode, urlparse, urlunparse

from azure.communication.callautomation import (AudioFormat,
                                                MediaStreamingAudioChannelType,
                                                MediaStreamingContentType,
                                                MediaStreamingOptions,
                                                StreamingTransportType,
                                                TextSource)
from azure.communication.callautomation.aio import CallAutomationClient
from azure.communication.callautomation import PhoneNumberIdentifier
from azure.eventgrid import EventGridEvent, SystemEventNames
from quart import Response

logger = logging.getLogger(__name__)

# Transfer status constants
TRANSFER_STATUS_PENDING = 'pending'
TRANSFER_STATUS_IN_PROGRESS = 'in_progress'
TRANSFER_STATUS_COMPLETED = 'completed'
TRANSFER_STATUS_FAILED = 'failed'


class AcsEventHandler:
    """Handles ACS event processing and call answering logic."""

    def __init__(self, config):
        self.acs_client = CallAutomationClient.from_connection_string(
            config["ACS_CONNECTION_STRING"]
        )
        self.call_connection_id = None
        self.phone_number = None

    async def process_incoming_call(self, events: list, host_url, config):
        """Processes incoming call events and answers calls with media streaming."""
        logger.info("incoming event data")

        for event_dict in events:
            event = EventGridEvent.from_dict(event_dict)
            logger.info("incoming event data --> %s", event.data)

            if (
                event.event_type
                == SystemEventNames.EventGridSubscriptionValidationEventName
            ):
                logger.info("Validating subscription")
                validation_code = event.data["validationCode"]
                return Response(
                    response=json.dumps({"validationResponse": validation_code}),
                    status=200,
                )

            if event.event_type == "Microsoft.Communication.IncomingCall":
                logger.info("Incoming call received: data=%s", event.data)

                caller_info = event.data["from"]
                caller_id = (
                    caller_info["phoneNumber"]["value"]
                    if caller_info["kind"] == "phoneNumber"
                    else caller_info["rawId"]
                )
                self.phone_number = caller_id
                logger.info("incoming call handler caller id: %s", caller_id)
                incoming_call_context = event.data["incomingCallContext"]
                query_parameters = urlencode({"callerId": caller_id})
                guid = uuid.uuid4()

                callback_events_uri = (
                    f"{config['ACS_DEV_TUNNEL']}/acs/callbacks"
                    if config["ACS_DEV_TUNNEL"]
                    else f"{host_url}/acs/callbacks"
                )
                callback_uri = f"{callback_events_uri}/{guid}?{query_parameters}"

                parsed_url = urlparse(callback_events_uri)
                websocket_url = urlunparse(
                    ("wss", parsed_url.netloc, "/acs/ws", "", "", "")
                )

                logger.info("callback url: %s", callback_uri)
                logger.info("websocket url: %s", websocket_url)

                media_streaming_options = MediaStreamingOptions(
                    transport_url=websocket_url,
                    transport_type=StreamingTransportType.WEBSOCKET,
                    content_type=MediaStreamingContentType.AUDIO,
                    audio_channel_type=MediaStreamingAudioChannelType.MIXED,
                    start_media_streaming=True,
                    enable_bidirectional=True,
                    audio_format=AudioFormat.PCM24_K_MONO,
                )

                result = await self.acs_client.answer_call(
                    incoming_call_context=incoming_call_context,
                    operation_context="incomingCall",
                    callback_url=callback_uri,
                    media_streaming=media_streaming_options,
                )
                self.call_connection_id = result.call_connection_id

                logger.info(
                    "Answered call for connection id: %s", result.call_connection_id
                )
                return Response(status=200)

        return Response(status=400)

    async def hang_up_call(self):
        """Hangs up an ongoing call."""
        try:
            if self.call_connection_id:
                await self.acs_client.hang_up_call(self.call_connection_id, for_everyone=True)
                logger.info("Successfully hung up call for connection id: %s", self.call_connection_id)
        except Exception as e:
            logger.error("Error hanging up call for connection id: %s, Error: %s", self.call_connection_id, e)

    async def make_outbound_call(self, session_id, host_url, config):
        logger.info("Making outbound call to phone number")
        # target = PhoneNumberIdentifier("8696728728")
        # source = PhoneNumberIdentifier(ACS_PHONE_NUMBER)
        callback_events_uri = (
                    f"{config['ACS_DEV_TUNNEL']}/agent-transfer-callback"
                    if config["ACS_DEV_TUNNEL"]
                    else f"{host_url}/agent-transfer-callback"
                )
        callback_uri = f"{callback_events_uri}/{session_id}"

        parsed_url = urlparse(callback_uri)
        websocket_url = urlunparse(
            ("wss", parsed_url.netloc, "/acs/agent-ws", "", "", "")
        )

        media_streaming_options = MediaStreamingOptions(
                    transport_url=websocket_url,
                    transport_type=StreamingTransportType.WEBSOCKET,
                    content_type=MediaStreamingContentType.AUDIO,
                    audio_channel_type=MediaStreamingAudioChannelType.MIXED,
                    start_media_streaming=True,
                    enable_bidirectional=True,
                    audio_format=AudioFormat.PCM24_K_MONO,
                )

        # Trigger the call
        result = await self.acs_client.create_call(target_participant=PhoneNumberIdentifier("+918696728728"),
                        callback_url= parsed_url.geturl(),
                        source_caller_id_number=PhoneNumberIdentifier("+18332400173"),
                        media_streaming=media_streaming_options
                 )
 
        # print(f"Call initiated with ID: {result.call_connection_id}")
        return result
        

    async def transfer_to_agent(self, call_connection_id: str, agent_phone: str) -> dict:
        """Transfer the call to a human agent via phone number.

        Args:
            call_connection_id: The ACS call connection ID
            agent_phone: The agent's phone number (E.164 format, e.g., +1234567890)

        Returns:
            dict with success status and details
        """
        logger.info("Transferring call %s to agent at %s", call_connection_id, agent_phone)

        try:
            call_connection = self.acs_client.get_call_connection(call_connection_id)

            target = PhoneNumberIdentifier(agent_phone)

            await call_connection.transfer_call_to_participant(
                target_participant=target,
                operation_context="transfer_to_agent"
            )

            logger.info("Transfer initiated for call %s to %s", call_connection_id, agent_phone)
            return {
                'success': True,
                'status': TRANSFER_STATUS_IN_PROGRESS,
                'message': f'Transfer initiated to {agent_phone}'
            }

        except Exception as e:
            logger.error("Failed to transfer call %s: %s", call_connection_id, e)
            return {
                'success': False,
                'status': TRANSFER_STATUS_FAILED,
                'error': str(e)
            }

    async def process_callback_events(self, context_id: str, raw_events: list, config):
        """Processes ACS callback events such as call connected, media started, etc."""
        for event in raw_events:
            event_data = event["data"]
            call_connection_id = event_data["callConnectionId"]

            logger.info(
                "Received Event:-> %s, Correlation Id:-> %s, CallConnectionId:-> %s",
                event["type"],
                event_data["correlationId"],
                call_connection_id,
            )

            if event["type"] == "Microsoft.Communication.CallConnected":
                properties = await self.acs_client.get_call_connection(
                    call_connection_id
                ).get_call_properties()

                logger.info(
                    "MediaStreamingSubscription:--> %s",
                    properties.media_streaming_subscription,
                )
                logger.info(
                    "Received CallConnected event for connection id: %s",
                    call_connection_id,
                )
                logger.info("CORRELATION ID:--> %s", event_data["correlationId"])
                logger.info("CALL CONNECTION ID:--> %s", call_connection_id)

            elif event["type"] == "Microsoft.Communication.MediaStreamingStarted":
                update = event_data["mediaStreamingUpdate"]
                logger.info(
                    "Media streaming content type:--> %s", update["contentType"]
                )
                logger.info(
                    "Media streaming status:--> %s", update["mediaStreamingStatus"]
                )
                logger.info(
                    "Media streaming status details:--> %s",
                    update["mediaStreamingStatusDetails"],
                )

            elif event["type"] == "Microsoft.Communication.MediaStreamingStopped":
                update = event_data["mediaStreamingUpdate"]
                logger.info(
                    "Media streaming content type:--> %s", update["contentType"]
                )
                logger.info(
                    "Media streaming status:--> %s", update["mediaStreamingStatus"]
                )
                logger.info(
                    "Media streaming status details:--> %s",
                    update["mediaStreamingStatusDetails"],
                )

            elif event["type"] == "Microsoft.Communication.MediaStreamingFailed":
                result_info = event_data["resultInformation"]
                logger.info(
                    "Code:-> %s, Subcode:-> %s",
                    result_info["code"],
                    result_info["subCode"],
                )
                logger.info("Message:-> %s", result_info["message"])

            elif event["type"] == "Microsoft.Communication.CallDisconnected":
                logger.info(
                    "CallDisconnected event received for: %s", call_connection_id
                )

            elif event["type"] == "Microsoft.Communication.CallTransferAccepted":
                logger.info(
                    "CallTransferAccepted event received for: %s", call_connection_id
                )
                # Transfer was successful - the call is now with the agent

            elif event["type"] == "Microsoft.Communication.CallTransferFailed":
                result_info = event_data.get("resultInformation", {})
                logger.error(
                    "CallTransferFailed for %s: Code=%s, SubCode=%s, Message=%s",
                    call_connection_id,
                    result_info.get("code"),
                    result_info.get("subCode"),
                    result_info.get("message")
                )

        return Response(status=200)
    
    async def process_callback_events_human_agent(self, raw_events: list, config):
        """Processes ACS callback events such as call connected, media started, etc."""
        for event in raw_events:
            event_data = event["data"]
            call_connection_id = event_data["callConnectionId"]

            logger.info(
                "Received Event Human agent:-> %s, Correlation Id:-> %s, CallConnectionId:-> %s",
                event["type"],
                event_data["correlationId"],
                call_connection_id,
            )

            if event["type"] == "Microsoft.Communication.CallConnected":
                # Play a greeting message using Text-to-Speech
                await self.play_greeting_to_agent(call_connection_id)
                print("Greeting played.")
            elif event["type"] == "Microsoft.Communication.CallDisconnected":
                logger.info(
                    "CallDisconnected event for human agent transfer received for: %s", call_connection_id
                )

        return Response(status=200)
    
    async def play_greeting_to_agent(
        self,
        call_connection_id: str
    ) -> bool:
        """
        Play a greeting message to the agent when they answer.
        
        Args:
            call_connection_id: The ACS call connection ID
            customer_name: Customer name for personalization
            conversation_summary: Brief summary of the conversation
            
        Returns:
            True if successful, False otherwise
        """
        try:
            call_conn = self.acs_client.get_call_connection(call_connection_id)
            
            # Build greeting message
            greeting = "You have a new customer transfer."
            # if customer_name:
            #     greeting += f" The customer's name is {customer_name}."
            # if conversation_summary:
            #     # Limit summary length for speech
            #     summary_preview = conversation_summary[:200]
            #     greeting += f" Here's a summary: {summary_preview}"
            greeting += " Please stand by to connect with the customer."
            
            # Create TTS source
            text_source = TextSource(
                text=greeting,
                voice_name="en-US-AriaNeural"
            )
            
            # Play the greeting
            await call_conn.play_media(
                play_source=text_source,
                play_to=[]
            )
            
            logger.info(
                "[AgentCallManager] Greeting played to call: %s",
                call_connection_id
            )
            return True
            
        except Exception as e:
            logger.error(
                "[AgentCallManager] Failed to play greeting: %s",
                e
            )
            return False
